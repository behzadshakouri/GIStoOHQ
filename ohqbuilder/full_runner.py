from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import math
from pathlib import Path
from typing import Callable

from .legacy_inputs import (
    LegacyWorkflowOptions,
    run_hydrology_preprocessing,
    run_legacy_input_workflow,
)
from .builders.watershed_builder import WatershedBuilder
from .input_downloader import download_all_inputs
from .hms_pipeline import build_hms_project
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .source_materializer import materialize_source_inputs
from .validation.input_validator import InputValidator
from .watershed_comparison import compare_watersheds
from .nhdplus_trace import NhdplusTraceError, trace_upstream_catchments
from .pour_point_candidates import PourPointCandidateError, generate_pour_point_candidates


class FullRunError(RuntimeError):
    """Raised when the download-to-OHQ workflow cannot finish."""


def existing_outlet_lonlat(root: str | Path, site: str) -> tuple[float, float]:
    """Read the single reviewed outlet and return its EPSG:4326 coordinate."""

    import geopandas as gpd

    path = Path(root).expanduser().resolve() / site / "outputs" / "outlet.shp"
    if not path.is_file():
        raise FullRunError(f"Reviewed outlet not found: {path}")
    frame = gpd.read_file(path)
    if len(frame) != 1 or frame.crs is None or frame.geometry.isna().any():
        raise FullRunError("Reviewed outlet must contain one point with a valid CRS")
    if not frame.geometry.geom_type.eq("Point").all():
        raise FullRunError("Reviewed outlet geometry must be a point")
    point = frame.to_crs("EPSG:4326").geometry.iloc[0]
    return float(point.x), float(point.y)


@dataclass(frozen=True)
class FullRunResult:
    output_path: Path
    hms_project_path: Path | None = None
    report_path: Path | None = None
    comparison_path: Path | None = None


def network_element_counts(watershed) -> dict[str, tuple[int, int]]:
    """Return ``(extracted, retained)`` counts for each modeled element type.

    The GeoPackage readers expose all extracted reach/junction features, while
    ``topology.gpkg`` contains only the elements retained after topology pruning.
    Keeping those meanings separate prevents the final summary from presenting
    extracted candidates as model-network elements.
    """

    collections = {
        "subbasin": list(getattr(watershed, "subbasins", []) or []),
        "reach": list(getattr(watershed, "reaches", []) or []),
        "junction": list(getattr(watershed, "junctions", []) or []),
    }
    topology = list(getattr(watershed, "topology", []) or [])
    retained = {kind: 0 for kind in collections}
    for link in topology:
        kind = str(getattr(link, "element_type", "") or "").strip().lower()
        if kind in retained:
            retained[kind] += 1
    if not topology:
        retained = {kind: len(items) for kind, items in collections.items()}
    return {kind: (len(items), retained[kind]) for kind, items in collections.items()}


def existing_legacy_hms_project(
    root: str | Path, site: str, project_name: str | None = None
) -> Path | None:
    """Locate the complete HMS project emitted by the legacy phase-2 writers."""

    root_path = Path(root).expanduser().resolve()
    names = dict.fromkeys(filter(None, (project_name, Path(site).name)))
    for name in names:
        candidate = root_path / "WS3_HMS" / name / f"{name}.hms"
        if candidate.is_file():
            return candidate.resolve()
    return None


def full_run_summary(
    watershed,
    ohq_path: str | Path,
    hms_path: str | Path,
    report_path: str | Path | None = None,
) -> str:
    """Return a concise final artifact and watershed-metrics summary."""
    subbasins = list(getattr(watershed, "subbasins", []) or [])
    counts = network_element_counts(watershed)
    area_km2 = sum(float(getattr(item, "area_km2", 0.0) or 0.0) for item in subbasins)
    lines = [
        "=" * 72,
        "FULL-RUN SUCCESS SUMMARY",
        "",
        "Watershed Area",
        f"  {area_km2:.4f} km²",
        "",
        "GIS Extraction",
        f"  Subbasins : {counts['subbasin'][0]}",
        f"  Reaches   : {counts['reach'][0]}",
        f"  Junctions : {counts['junction'][0]}",
        "",
        "Final Model Network",
        f"  Subbasins : {counts['subbasin'][1]}",
        f"  Reaches   : {counts['reach'][1]}",
        f"  Junctions : {counts['junction'][1]}",
        "",
        "Products",
        f"  ✓ OHQ model       : {Path(ohq_path).expanduser().resolve()}",
        f"  ✓ HEC-HMS project : {Path(hms_path).expanduser().resolve()}",
    ]
    if report_path is not None:
        lines.append(
            f"  ✓ Watershed report: {Path(report_path).expanduser().resolve()}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)


def write_watershed_report(
    watershed,
    ohq_path: str | Path,
    hms_path: str | Path,
    output_path: str | Path | None = None,
    comparison_paths: list[str | Path] | None = None,
) -> Path:
    """Write a portable HTML summary for model review and regression baselines."""

    ohq = Path(ohq_path).expanduser().resolve()
    hms = Path(hms_path).expanduser().resolve()
    report = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else ohq.with_name("watershed_report.html")
    )
    subbasins = list(getattr(watershed, "subbasins", []) or [])
    counts = network_element_counts(watershed)
    area = sum(float(getattr(item, "area_km2", 0.0) or 0.0) for item in subbasins)

    comparison_rows = []
    for comparison_path in comparison_paths or []:
        path = Path(comparison_path).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        best = payload.get("best_match") or {}
        comparison_rows.append(
            "<tr>"
            f"<td>{escape(str(payload.get('reference_layer', path.stem)))}</td>"
            f"<td>{escape(str(best.get('reference_id', '—')))}</td>"
            f"<td>{float(best.get('iou', 0.0)):.3f}</td>"
            f"<td>{float(best.get('commission_area_km2', 0.0)):.3f}</td>"
            f"<td>{float(best.get('omission_area_km2', 0.0)):.3f}</td>"
            f"<td>{float(best.get('boundary_hausdorff_m', 0.0)):.1f}</td>"
            f"<td><code>{escape(str(payload.get('disagreement_geopackage') or '—'))}</code></td>"
            "</tr>"
        )
    comparison_section = ""
    if comparison_rows:
        comparison_section = (
            "<h2>Boundary comparisons</h2>"
            "<p>Reference matches are review evidence, not automatically accepted boundaries.</p>"
            "<table><thead><tr><th>Reference layer</th><th>Reference ID</th><th>IoU</th>"
            "<th>Generated only (km²)</th><th>Reference only (km²)</th>"
            "<th>Hausdorff (m)</th><th>Disagreement map</th></tr></thead><tbody>"
            + "".join(comparison_rows)
            + "</tbody></table>"
        )

    def value(item, attribute: str, digits: int = 2) -> str:
        raw = getattr(item, attribute, None)
        return "—" if raw is None else f"{float(raw):.{digits}f}"

    rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(getattr(item, 'name', getattr(item, 'id', ''))))}</td>"
        f"<td>{value(item, 'area_km2', 4)}</td>"
        f"<td>{value(item, 'curve_number', 1)}</td>"
        f"<td>{value(item, 'slope_pct', 2)}</td>"
        f"<td>{value(item, 'flow_len_ft', 0)}</td>"
        f"<td>{value(item, 'tc_min', 1)}</td>"
        f"<td>{value(item, 'lag_min', 1)}</td>"
        "</tr>"
        for item in subbasins
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>GIStoOHQ Watershed Report</title>
<style>body{{font:16px sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:.45rem;text-align:right}}
th:first-child,td:first-child{{text-align:left}}code{{overflow-wrap:anywhere}}</style></head><body>
<h1>GIStoOHQ Watershed Report</h1>
<h2>Watershed Area</h2><p><strong>{area:.4f} km²</strong></p>
<h2>GIS Extraction</h2><ul><li>Subbasins: {counts['subbasin'][0]}</li>
<li>Reaches: {counts['reach'][0]}</li><li>Junctions: {counts['junction'][0]}</li></ul>
<h2>Final Model Network</h2><ul><li>Subbasins: {counts['subbasin'][1]}</li>
<li>Reaches: {counts['reach'][1]}</li><li>Junctions: {counts['junction'][1]}</li></ul>
<h2>Outlet snap quality</h2><ul><li>GREEN: less than 20 m</li>
<li>YELLOW: 20–75 m</li><li>RED: greater than 75 m</li></ul>
{comparison_section}
<h2>Subbasin parameters</h2><table><thead><tr><th>Subbasin</th><th>Area (km²)</th>
<th>CN</th><th>Slope (%)</th><th>Flow path (ft)</th><th>Tc (min)</th><th>Lag (min)</th>
</tr></thead><tbody>{rows}</tbody></table>
<h2>Artifacts</h2><p>OHQ: <code>{escape(str(ohq))}</code></p>
<p>HEC-HMS: <code>{escape(str(hms))}</code></p>
<p><em>Review the snapped outlet, longest flow path, topology, parameters, and design storms before use.</em></p>
</body></html>"""
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(document, encoding="utf-8")
    return report


def acquisition_bounds(path: str | Path) -> tuple[float, float, float, float]:
    """Read the bounding box of GeoJSON acquisition geometry in EPSG:4326."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    coordinates: list[tuple[float, float]] = []

    def collect(value) -> None:
        if (
            isinstance(value, list)
            and len(value) >= 2
            and all(isinstance(item, (int, float)) for item in value[:2])
        ):
            coordinates.append((float(value[0]), float(value[1])))
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for feature in data.get("features", []):
        collect((feature.get("geometry") or {}).get("coordinates", []))
    if not coordinates:
        raise FullRunError(f"Acquisition GeoJSON contains no coordinates: {path}")
    xs, ys = zip(*coordinates)
    return min(xs), min(ys), max(xs), max(ys)


def buffer_covering_bounds(
    lon: float, lat: float, bounds: tuple[float, float, float, float]
) -> float:
    """Return an outlet-centered query radius covering all acquisition corners."""
    minx, miny, maxx, maxy = bounds
    meters_per_lon = 111_320.0 * max(math.cos(math.radians(lat)), 0.1)
    return max(
        math.hypot((x - lon) * meters_per_lon, (y - lat) * 111_320.0)
        for x in (minx, maxx)
        for y in (miny, maxy)
    )


def bounds_covering_outlet(
    bounds: tuple[float, float, float, float],
    lon: float,
    lat: float,
    *,
    margin_m: float = 500.0,
) -> tuple[float, float, float, float]:
    """Expand acquisition bounds so routing rasters safely contain the outlet.

    A user-drawn polygon may not contain the selected outlet, and exact clipping
    to that polygon's bounds previously produced a DEM/flow-accumulation raster
    that could not be used by the outlet written from the same UI coordinates.
    """

    minx, miny, maxx, maxy = bounds
    if minx <= lon <= maxx and miny <= lat <= maxy:
        return bounds

    lat_margin = max(margin_m, 0.0) / 111_320.0
    lon_margin = lat_margin / max(math.cos(math.radians(lat)), 0.1)
    return (
        min(minx, lon - lon_margin),
        min(miny, lat - lat_margin),
        max(maxx, lon + lon_margin),
        max(maxy, lat + lat_margin),
    )


def run_full_pipeline(
    root: str | Path,
    site: str,
    *,
    lon: float,
    lat: float,
    project_name: str | None = None,
    output_path: str | Path | None = None,
    script_dir: str | Path | None = None,
    buffer_m: float | None = None,
    target_crs: str | None = None,
    site_id: str | None = None,
    download_dir: str | Path | None = None,
    max_tiles: int | None = None,
    max_file_size_mb: float | None = None,
    soil_pixel_size: float = 0.0003,
    soil_top_depth: float = 30.0,
    acquisition_area: str | Path | None = None,
    use_reviewed_pour_points: bool = False,
    nhdplus_snap_distance_m: float = 50.0,
    use_existing_outlet: bool = False,
    progress: Callable[[str], None] | None = None,
) -> FullRunResult:
    """Download, materialize, prepare, validate, and build a project in one call."""

    def emit(message: str) -> None:
        if progress:
            progress(message)
        else:
            print(message, flush=True)

    try:
        emit("Starting full-run pipeline.")
        if use_existing_outlet:
            lon, lat = existing_outlet_lonlat(root, site)
            emit("Outlet source: existing outputs/outlet.shp")
            emit(f"Reviewed outlet in EPSG:4326: {lon:.10f}, {lat:.10f}")
        else:
            emit("Outlet source: CLI longitude/latitude (outlet.shp will be recreated)")
        selected_bounds = acquisition_bounds(acquisition_area) if acquisition_area else None
        buffer_was_supplied = buffer_m is not None
        if buffer_m is None:
            buffer_m = 5000.0
        if selected_bounds is not None:
            original_bounds = selected_bounds
            selected_bounds = bounds_covering_outlet(selected_bounds, lon, lat)
            if selected_bounds != original_bounds:
                emit(
                    "Expanded acquisition clipping bounds to retain a 500 m safety margin "
                    "around the selected outlet."
                )
            required_buffer = buffer_covering_bounds(lon, lat, selected_bounds)
            # With an explicit area, its radius is the useful default.  Retain
            # a larger value only when the caller deliberately supplied one.
            if not buffer_was_supplied:
                buffer_m = required_buffer * 1.05
            else:
                buffer_m = max(buffer_m, required_buffer * 1.05)
            emit(
                f"Using acquisition area {Path(acquisition_area).expanduser().resolve()} "
                f"for downloads and clipping (query buffer {buffer_m:.0f} m)."
            )
        # Step 1: download every supported source product before any merge/clip.
        fetched = download_all_inputs(
            root,
            site,
            lon=lon,
            lat=lat,
            site_id=site_id,
            download_dir=download_dir,
            buffer_m=buffer_m,
            max_tiles=max_tiles,
            max_file_size_mb=max_file_size_mb,
            soil_pixel_size=soil_pixel_size,
            soil_top_depth=soil_top_depth,
            progress=emit,
            use_existing_outlet=use_existing_outlet,
        )
        # Step 2: merge, project, and clip the downloaded DEM and hydrography.
        emit("[4/6] Mosaicking DEM and clipping hydrography...")
        materialized = materialize_source_inputs(
            root,
            site,
            source_dir=fetched.download_dir,
            target_crs=target_crs,
            clip_bounds=selected_bounds,
            clip_center_lon=lon,
            clip_center_lat=lat,
            clip_buffer_m=buffer_m,
        )
        materialized_hydro = getattr(materialized, "hydro", None)
        catchment_path = getattr(materialized_hydro, "catchment_path", None)
        nhdplus_candidate = None
        if catchment_path is not None:
            try:
                nhdplus_candidate = trace_upstream_catchments(
                    materialized_hydro.output_path,
                    catchment_path,
                    Path(root).expanduser().resolve()
                    / site
                    / "outputs"
                    / "NHDPlus_upstream_candidate.gpkg",
                    outlet_lon=lon,
                    outlet_lat=lat,
                    maximum_snap_distance_m=nhdplus_snap_distance_m,
                )
                emit(f"Wrote NHDPlus upstream watershed candidate: {nhdplus_candidate}")
                try:
                    candidates = generate_pour_point_candidates(
                        nhdplus_candidate,
                        Path(root).expanduser().resolve()
                        / site
                        / "outputs"
                        / "pour_point_candidates.gpkg",
                        outlet_lon=lon,
                        outlet_lat=lat,
                    )
                    emit(f"Wrote pour-point review candidates: {candidates}")
                except PourPointCandidateError as exc:
                    emit(f"Pour-point candidate generation requires review: {exc}")
            except NhdplusTraceError as exc:
                emit(f"NHDPlus upstream trace requires review: {exc}")
        # Step 3: generate the GIS-derived model inputs.
        if use_reviewed_pour_points:
            reviewed_points = (
                Path(root).expanduser().resolve() / site / "outputs" / "pour_points.shp"
            )
            if not reviewed_points.is_file():
                raise FullRunError(
                    "--use-reviewed-pour-points requires outputs/pour_points.shp; "
                    "run promote-pour-points after reviewing candidates first."
                )
            emit(f"Using reviewed pour points without automatic replacement: {reviewed_points}")
        options = LegacyWorkflowOptions(
            auto_outlet=True,
            auto_pour_points=not use_reviewed_pour_points,
            refresh_auto_pour_points=not use_reviewed_pour_points,
            child_options={"MAX_OUTLET_SNAP_M": nhdplus_snap_distance_m},
        )
        emit("[5/6] Running hydrology preprocessing and GIS phases...")
        run_hydrology_preprocessing(root, site, script_dir, options)
        run_legacy_input_workflow(root, site, script_dir, "all", options)
        comparison_path = None
        comparison_paths = []
        wbd_reference = getattr(materialized, "wbd_reference", None)
        generated_boundary = Path(root).expanduser().resolve() / site / "outputs" / "watershed_boundary.gpkg"
        if wbd_reference is not None and generated_boundary.is_file():
            comparison_path = compare_watersheds(
                generated_boundary,
                wbd_reference,
                generated_boundary.with_name("watershed_wbd_comparison.json"),
                disagreement_path=generated_boundary.with_name(
                    "watershed_wbd_disagreement.gpkg"
                ),
            )
            emit(f"Wrote WBD comparison metrics: {comparison_path}")
            comparison_paths.append(comparison_path)
        if nhdplus_candidate is not None and generated_boundary.is_file():
            nhd_comparison = compare_watersheds(
                generated_boundary,
                nhdplus_candidate,
                generated_boundary.with_name("watershed_nhdplus_comparison.json"),
                reference_layer="upstream_boundary",
                reference_id_fields=("outlet_reach_id",),
                disagreement_path=generated_boundary.with_name(
                    "watershed_nhdplus_disagreement.gpkg"
                ),
            )
            emit(f"Wrote NHDPlus comparison metrics: {nhd_comparison}")
            comparison_paths.append(nhd_comparison)
        # Step 4: validate the generated inputs and write the OHQ file.
        emit("[6/6] Validating inputs and building OHQ...")
        settings = BuilderSettings.from_args(root, site, project_name=project_name)
        validation = InputValidator().validate(settings)
        if not validation.ok:
            raise FullRunError(
                "Generated inputs failed validation: " + "; ".join(validation.errors)
            )
        requested_output = Path(output_path).expanduser().resolve() if output_path else None
        built = build_ohq_project(settings, output_path=requested_output)
        if not built:
            raise FullRunError("OHQ builder did not produce an output path.")
        hms_path = existing_legacy_hms_project(root, site, project_name)
        if hms_path is None:
            hms_path = Path(build_hms_project(settings).project_file)
        watershed = WatershedBuilder(settings).build()
        report_path = write_watershed_report(
            watershed, built, hms_path, comparison_paths=comparison_paths
        )
        emit(full_run_summary(watershed, built, hms_path, report_path))
        return FullRunResult(Path(built), hms_path, report_path, comparison_path)
    except FullRunError:
        raise
    except Exception as exc:
        raise FullRunError(str(exc)) from exc

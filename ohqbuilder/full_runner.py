from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable

from .legacy_inputs import (
    LegacyWorkflowOptions,
    run_hydrology_preprocessing,
    run_legacy_input_workflow,
)
from .input_downloader import download_all_inputs
from .hms_pipeline import build_hms_project
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .source_materializer import materialize_source_inputs
from .validation.input_validator import InputValidator


class FullRunError(RuntimeError):
    """Raised when the download-to-OHQ workflow cannot finish."""


@dataclass(frozen=True)
class FullRunResult:
    output_path: Path
    hms_project_path: Path | None = None


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
    buffer_m: float = 5000.0,
    target_crs: str | None = None,
    site_id: str | None = None,
    download_dir: str | Path | None = None,
    max_tiles: int | None = None,
    max_file_size_mb: float | None = None,
    soil_pixel_size: float = 0.0003,
    soil_top_depth: float = 30.0,
    acquisition_area: str | Path | None = None,
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
        selected_bounds = acquisition_bounds(acquisition_area) if acquisition_area else None
        if selected_bounds is not None:
            original_bounds = selected_bounds
            selected_bounds = bounds_covering_outlet(selected_bounds, lon, lat)
            if selected_bounds != original_bounds:
                emit(
                    "Expanded acquisition clipping bounds to retain a 500 m safety margin "
                    "around the selected outlet."
                )
            required_buffer = buffer_covering_bounds(lon, lat, selected_bounds)
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
        )
        # Step 2: merge, project, and clip the downloaded DEM and hydrography.
        emit("[4/6] Mosaicking DEM and clipping hydrography...")
        materialize_source_inputs(
            root,
            site,
            source_dir=fetched.download_dir,
            target_crs=target_crs,
            clip_bounds=selected_bounds,
        )
        # Step 3: generate the GIS-derived model inputs.
        options = LegacyWorkflowOptions(
            auto_outlet=True,
            auto_pour_points=True,
            refresh_auto_pour_points=True,
        )
        emit("[5/6] Running hydrology preprocessing and GIS phases...")
        run_hydrology_preprocessing(root, site, script_dir, options)
        run_legacy_input_workflow(root, site, script_dir, "all", options)
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
        hms = build_hms_project(settings)
        emit(f"HEC-HMS project complete: {hms.project_file}")
        emit(f"Full-run complete: {built}")
        return FullRunResult(Path(built), hms.project_file)
    except FullRunError:
        raise
    except Exception as exc:
        raise FullRunError(str(exc)) from exc

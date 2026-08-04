from __future__ import annotations

import csv
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import urllib.parse
import urllib.request


class DocumentedWatershedError(RuntimeError):
    """Raised when a documented watershed reference cannot be verified."""


REFERENCE_FILENAME = "DocumentedWatershed_reference.gpkg"
REFERENCE_LAYER = "documented_watershed_reference"


def export_boundary_vertices(
    source: str | Path,
    output_csv: str | Path,
    *,
    layer: str | None = None,
    target_crs: str | None = None,
) -> Path:
    """Write every polygon ring vertex to a lossless, structured CSV.

    Unlike examples that select the first feature and exterior ring, this exporter
    preserves feature, polygon-part, and ring identity, including interior rings.
    """

    _require_gis()
    import geopandas as gpd

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise DocumentedWatershedError(f"Watershed dataset does not exist: {source_path}")
    frame = gpd.read_file(source_path, layer=layer) if layer else gpd.read_file(source_path)
    if frame.empty or frame.crs is None:
        raise DocumentedWatershedError("The watershed dataset must be non-empty and define a CRS.")
    if target_crs:
        frame = frame.to_crs(target_crs)
    if not frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise DocumentedWatershedError("Every watershed feature must be a Polygon or MultiPolygon.")

    target = Path(output_csv).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["feature_id", "part_id", "ring_id", "ring_type", "vertex_id", "x", "y"])
        for feature_id, geometry in enumerate(frame.geometry):
            polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
            for part_id, polygon in enumerate(polygons):
                rings = [("exterior", polygon.exterior), *[("interior", ring) for ring in polygon.interiors]]
                for ring_id, (ring_type, ring) in enumerate(rings):
                    for vertex_id, coordinate in enumerate(ring.coords):
                        writer.writerow(
                            [feature_id, part_id, ring_id, ring_type, vertex_id, coordinate[0], coordinate[1]]
                        )
    return target


def _require_gis() -> None:
    if importlib.util.find_spec("geopandas") is None:
        raise DocumentedWatershedError(
            "Importing a documented boundary requires GIS dependencies; "
            "install with `pip install -e .[gis]`."
        )


def _arcgis_geojson(
    layer_url: str,
    *,
    outlet_lon: float,
    outlet_lat: float,
    where: str,
    timeout: float,
) -> dict:
    params = {
        "f": "geojson",
        "where": where,
        "geometry": f"{outlet_lon:.10f},{outlet_lat:.10f}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
    }
    url = f"{layer_url.rstrip('/')}/query?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise DocumentedWatershedError(
            f"Documented watershed service request failed: {exc}"
        ) from exc
    if payload.get("error"):
        raise DocumentedWatershedError(
            f"Documented watershed service returned an error: {payload['error']}"
        )
    return payload


def import_documented_watershed(
    source: str | Path,
    output_path: str | Path,
    *,
    outlet_lon: float,
    outlet_lat: float,
    layer: str | None = None,
    name_field: str | None = None,
    name: str | None = None,
    where: str = "1=1",
    source_title: str,
    source_organization: str,
    source_url: str | None = None,
    license_text: str | None = None,
    timeout: float = 60.0,
) -> Path:
    """Import a cited polygon reference and retain its provenance.

    ``source`` may be a local vector dataset or an ArcGIS FeatureServer/MapServer
    layer URL ending in a numeric layer id. Images and PDF maps are intentionally
    not accepted because they do not provide surveyable polygon coordinates.
    """

    _require_gis()
    import geopandas as gpd
    from shapely.geometry import Point

    source_text = str(source)
    is_service = source_text.lower().startswith(("http://", "https://"))
    if is_service:
        if not urllib.parse.urlparse(source_text).path.rstrip("/").split("/")[-1].isdigit():
            raise DocumentedWatershedError(
                "ArcGIS source must be a layer URL ending in a numeric layer id, "
                "not the service catalog/root URL."
            )
        payload = _arcgis_geojson(
            source_text,
            outlet_lon=outlet_lon,
            outlet_lat=outlet_lat,
            where=where,
            timeout=timeout,
        )
        features = payload.get("features") or []
        if not features:
            raise DocumentedWatershedError(
                "The service returned no watershed polygon containing the outlet."
            )
        frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
        resolved_source = source_text
    else:
        path = Path(source).expanduser().resolve()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf"}:
            raise DocumentedWatershedError(
                "A map image/PDF is evidence, but not a georeferenced polygon boundary. "
                "Obtain the publisher's GIS layer or digitize and document a derived layer."
            )
        if not path.exists():
            raise DocumentedWatershedError(f"Reference dataset does not exist: {path}")
        frame = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
        resolved_source = str(path)

    if frame.empty or frame.crs is None:
        raise DocumentedWatershedError(
            "The documented watershed dataset must be non-empty and define a CRS."
        )
    polygonal = frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    frame = frame[polygonal & ~frame.geometry.is_empty].copy()
    if frame.empty:
        raise DocumentedWatershedError("The documented watershed dataset has no polygons.")

    if name is not None:
        if not name_field or name_field not in frame.columns:
            raise DocumentedWatershedError(
                f"--name requires a valid --name-field; available fields: "
                f"{', '.join(map(str, frame.columns))}"
            )
        frame = frame[
            frame[name_field].astype(str).str.strip().str.casefold()
            == name.strip().casefold()
        ].copy()
        if frame.empty:
            raise DocumentedWatershedError(
                f"No feature has {name_field}={name!r}."
            )

    outlet = gpd.GeoSeries(
        [Point(outlet_lon, outlet_lat)], crs="EPSG:4326"
    ).to_crs(frame.crs)[0]
    containing = frame[frame.geometry.covers(outlet)].copy()
    if containing.empty:
        raise DocumentedWatershedError(
            "No selected documented watershed contains the modeled outlet. "
            "Verify the outlet, layer, CRS, and named feature."
        )
    frame = containing
    if len(frame) > 1:
        metric_crs = frame.to_crs("EPSG:4326").estimate_utm_crs()
        if metric_crs is None:
            raise DocumentedWatershedError(
                "Could not determine a projected CRS for reference selection."
            )
        frame = frame.assign(_area=frame.to_crs(metric_crs).geometry.area)
        frame = frame.sort_values("_area").head(1)
        frame = frame.drop(columns=["_area"])

    retrieved = datetime.now(timezone.utc).isoformat()
    frame["ref_title"] = source_title
    frame["ref_org"] = source_organization
    frame["ref_url"] = source_url or (source_text if is_service else "")
    frame["ref_kind"] = "documented_named_watershed"
    frame["retrieved"] = retrieved
    frame["license"] = license_text or "not specified"

    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_file(target, layer=REFERENCE_LAYER, driver="GPKG")
    metadata = {
        "reference_kind": "documented_named_watershed",
        "source_title": source_title,
        "source_organization": source_organization,
        "source_url": source_url or (source_text if is_service else None),
        "source_dataset": resolved_source,
        "source_layer": layer,
        "selection_name_field": name_field,
        "selection_name": name,
        "service_where": where if is_service else None,
        "outlet_lon": outlet_lon,
        "outlet_lat": outlet_lat,
        "feature_count": len(frame),
        "retrieved_utc": retrieved,
        "license": license_text or "not specified",
        "important": (
            "This is a cited reference boundary. It is not automatically substituted "
            "for the DEM-derived watershed."
        ),
    }
    target.with_suffix(".json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return target

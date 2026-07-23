from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

METERS_PER_DEGREE = 111_320.0
AcquisitionMode = Literal["outlet_buffer", "oriented_outlet_buffer"]


class DemAcquisitionError(RuntimeError):
    """Raised when a DEM acquisition area cannot be created."""


@dataclass(frozen=True)
class DemAcquisitionArea:
    mode: str
    output_path: Path
    bounds: tuple[float, float, float, float]
    area_km2: float


def _degrees_per_meter(lat: float) -> tuple[float, float]:
    lat_deg = 1.0 / METERS_PER_DEGREE
    lon_deg = 1.0 / (METERS_PER_DEGREE * max(0.1, abs(math.cos(math.radians(lat)))))
    return lon_deg, lat_deg


def _axis_aligned_rectangle(lon: float, lat: float, half_width_m: float, half_height_m: float) -> list[tuple[float, float]]:
    lon_deg, lat_deg = _degrees_per_meter(lat)
    dx = half_width_m * lon_deg
    dy = half_height_m * lat_deg
    return [
        (lon - dx, lat - dy),
        (lon + dx, lat - dy),
        (lon + dx, lat + dy),
        (lon - dx, lat + dy),
        (lon - dx, lat - dy),
    ]


def _oriented_rectangle(lon: float, lat: float, upstream_m: float, downstream_m: float, lateral_m: float, azimuth_deg: float) -> list[tuple[float, float]]:
    """Return a lon/lat rectangle aligned with an upstream azimuth.

    ``azimuth_deg`` is degrees clockwise from north pointing from the outlet toward upstream.
    """

    theta = math.radians(azimuth_deg)
    ux = math.sin(theta)
    uy = math.cos(theta)
    lx = math.cos(theta)
    ly = -math.sin(theta)
    corners_m = [
        (-downstream_m * ux - lateral_m * lx, -downstream_m * uy - lateral_m * ly),
        (-downstream_m * ux + lateral_m * lx, -downstream_m * uy + lateral_m * ly),
        (upstream_m * ux + lateral_m * lx, upstream_m * uy + lateral_m * ly),
        (upstream_m * ux - lateral_m * lx, upstream_m * uy - lateral_m * ly),
    ]
    lon_deg, lat_deg = _degrees_per_meter(lat)
    coords = [(lon + x * lon_deg, lat + y * lat_deg) for x, y in corners_m]
    coords.append(coords[0])
    return coords


def _write_geojson_polygon(path: Path, coords: list[tuple[float, float]], properties: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    feature = {
        "type": "FeatureCollection",
        "name": path.stem,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [[[x, y] for x, y in coords]]},
            }
        ],
    }
    path.write_text(json.dumps(feature, indent=2), encoding="utf-8")


def create_outlet_buffer_area(
    lon: float,
    lat: float,
    output_path: str | Path,
    *,
    upstream_km: float = 25.0,
    downstream_km: float = 3.0,
    lateral_km: float = 5.0,
    azimuth_deg: float | None = None,
) -> DemAcquisitionArea:
    """Create an initial DEM acquisition polygon from an outlet point.

    With ``azimuth_deg`` this creates an elongated rectangle suitable for outlet-only
    workflows when an upstream network trace is not available yet. Without an azimuth,
    it creates an axis-aligned rectangle using the larger of upstream/downstream as the
    north-south half-height and ``lateral_km`` as the east-west half-width.
    """

    if upstream_km <= 0 or downstream_km < 0 or lateral_km <= 0:
        raise DemAcquisitionError("upstream_km and lateral_km must be positive; downstream_km cannot be negative.")
    output = Path(output_path).expanduser().resolve()
    if azimuth_deg is None:
        mode = "outlet_buffer"
        coords = _axis_aligned_rectangle(lon, lat, lateral_km * 1000.0, max(upstream_km, downstream_km) * 1000.0)
    else:
        mode = "oriented_outlet_buffer"
        coords = _oriented_rectangle(lon, lat, upstream_km * 1000.0, downstream_km * 1000.0, lateral_km * 1000.0, azimuth_deg)
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    area_km2 = (upstream_km + downstream_km) * (2 * lateral_km)
    _write_geojson_polygon(
        output,
        coords,
        {
            "mode": mode,
            "outlet_lon": lon,
            "outlet_lat": lat,
            "upstream_km": upstream_km,
            "downstream_km": downstream_km,
            "lateral_km": lateral_km,
            "azimuth_deg": azimuth_deg,
            "area_km2": area_km2,
        },
    )
    return DemAcquisitionArea(mode, output, (min(xs), min(ys), max(xs), max(ys)), area_km2)


@dataclass(frozen=True)
class DemTileManifest:
    output_path: Path
    selected_count: int
    acquisition_bounds: tuple[float, float, float, float]


def _geometry_coords(geometry: dict[str, object]) -> list[tuple[float, float]]:
    gtype = geometry.get("type")
    coordinates = geometry.get("coordinates")
    points: list[tuple[float, float]] = []

    def walk(value: object) -> None:
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            points.append((float(value[0]), float(value[1])))
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    if not isinstance(geometry, dict) or not isinstance(gtype, str):
        raise DemAcquisitionError("GeoJSON feature has an invalid geometry.")
    walk(coordinates)
    if not points:
        raise DemAcquisitionError("GeoJSON geometry does not contain coordinates.")
    return points


def _bounds_from_geojson(path: Path) -> tuple[float, float, float, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list) or not features:
        raise DemAcquisitionError(f"Expected a GeoJSON FeatureCollection with at least one feature: {path}")
    points: list[tuple[float, float]] = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            continue
        points.extend(_geometry_coords(feature["geometry"]))
    if not points:
        raise DemAcquisitionError(f"No geometry coordinates found in: {path}")
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def build_dem_tile_manifest(
    acquisition_area: str | Path,
    tile_index: str | Path,
    output_path: str | Path,
    *,
    url_field: str = "url",
    path_field: str = "path",
) -> DemTileManifest:
    """Select tile-index features intersecting an acquisition area and write a manifest.

    This intentionally uses GeoJSON feature bounds so it remains lightweight for
    terminal/UI preview workflows. Precise polygon intersection can be added later
    behind the same manifest contract when full GIS dependencies are available.
    """

    acquisition_path = Path(acquisition_area).expanduser().resolve()
    index_path = Path(tile_index).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    acquisition_bounds = _bounds_from_geojson(acquisition_path)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        raise DemAcquisitionError(f"Expected a GeoJSON tile-index FeatureCollection: {index_path}")

    items: list[dict[str, object]] = []
    tiles: list[str] = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            continue
        tile_bounds = _bounds_from_geojson_feature(feature)
        if not _intersects(acquisition_bounds, tile_bounds):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        url = properties.get(url_field) or properties.get("downloadURL") or properties.get("downloadUrl")
        tile_path = properties.get(path_field) or properties.get("file")
        title = properties.get("title") or properties.get("name") or tile_path or url
        item = {"title": title, "bounds": tile_bounds}
        if url:
            item["url"] = url
        if tile_path:
            item["path"] = tile_path
            tiles.append(str(tile_path))
        items.append(item)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "acquisition_area": str(acquisition_path),
                "tile_index": str(index_path),
                "acquisition_bounds": acquisition_bounds,
                "tiles": tiles,
                "items": items,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return DemTileManifest(output, len(items), acquisition_bounds)


def _bounds_from_geojson_feature(feature: dict[str, object]) -> tuple[float, float, float, float]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise DemAcquisitionError("Tile-index feature is missing geometry.")
    points = _geometry_coords(geometry)
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)

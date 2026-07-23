from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

METERS_PER_DEGREE = 111_320.0
AcquisitionMode = Literal["outlet_buffer", "oriented_outlet_buffer", "upstream_network"]


class DemAcquisitionError(RuntimeError):
    """Raised when a DEM acquisition area cannot be created."""


@dataclass(frozen=True)
class DemAcquisitionArea:
    mode: str
    output_path: Path
    bounds: tuple[float, float, float, float]
    area_km2: float


@dataclass(frozen=True)
class SnappedOutlet:
    raw_lon: float
    raw_lat: float
    snapped_lon: float
    snapped_lat: float
    distance_m: float
    output_path: Path | None = None


def _closest_point_on_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> tuple[float, float]:
    dx = bx - ax
    dy = by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return ax, ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return ax + t * dx, ay + t * dy


def _write_geojson_point(path: Path, lon: float, lat: float, properties: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    feature = {
        "type": "FeatureCollection",
        "name": path.stem,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [{
            "type": "Feature",
            "properties": properties,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }],
    }
    path.write_text(json.dumps(feature, indent=2), encoding="utf-8")


def write_outlet_point(lon: float, lat: float, output_path: str | Path, *, source: str = "raw") -> Path:
    """Write an EPSG:4326 outlet point GeoJSON for workflow handoffs/previews."""

    output = Path(output_path).expanduser().resolve()
    _write_geojson_point(output, lon, lat, {"source": source, "outlet_lon": lon, "outlet_lat": lat})
    return output


def snap_outlet_to_flowlines(
    lon: float,
    lat: float,
    flowline_path: str | Path,
    *,
    snap_distance_m: float = 500.0,
    output_path: str | Path | None = None,
) -> SnappedOutlet:
    """Snap an outlet point to the nearest EPSG:4326 GeoJSON flowline segment."""

    if snap_distance_m <= 0:
        raise DemAcquisitionError("snap_distance_m must be positive.")
    flowline = Path(flowline_path).expanduser().resolve()
    data = json.loads(flowline.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DemAcquisitionError(f"Expected GeoJSON flowlines: {flowline}")
    best: tuple[float, float, float] | None = None
    for feature in _geojson_features(data, flowline):
        points = _feature_points(feature)
        if len(points) < 2:
            continue
        local = [_lonlat_to_local_m(point_lon, point_lat, lon, lat) for point_lon, point_lat in points]
        for (ax, ay), (bx, by) in zip(local, local[1:]):
            sx, sy = _closest_point_on_segment(0.0, 0.0, ax, ay, bx, by)
            distance = math.hypot(sx, sy)
            if best is None or distance < best[2]:
                snapped_lon, snapped_lat = _local_m_to_lonlat(sx, sy, lon, lat)
                best = (snapped_lon, snapped_lat, distance)
    if best is None:
        raise DemAcquisitionError("No line segments found in flowline GeoJSON for outlet snapping.")
    snapped_lon, snapped_lat, distance = best
    if distance > snap_distance_m:
        raise DemAcquisitionError(
            f"Nearest flowline is {distance:g} m from outlet, beyond snap_distance_m={snap_distance_m:g}."
        )
    output = Path(output_path).expanduser().resolve() if output_path is not None else None
    if output is not None:
        _write_geojson_point(
            output,
            snapped_lon,
            snapped_lat,
            {
                "raw_lon": lon,
                "raw_lat": lat,
                "snap_distance_m": distance,
                "max_snap_distance_m": snap_distance_m,
                "flowline_path": str(flowline),
            },
        )
    return SnappedOutlet(lon, lat, snapped_lon, snapped_lat, distance, output)


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


def _lonlat_to_local_m(lon: float, lat: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    meters_per_lon, meters_per_lat = _meters_per_degree_at_lat(origin_lat)
    return (lon - origin_lon) * meters_per_lon, (lat - origin_lat) * meters_per_lat


def _local_m_to_lonlat(x: float, y: float, origin_lon: float, origin_lat: float) -> tuple[float, float]:
    meters_per_lon, meters_per_lat = _meters_per_degree_at_lat(origin_lat)
    return origin_lon + x / meters_per_lon, origin_lat + y / meters_per_lat


def _principal_axis(points_m: list[tuple[float, float]], outlet_m: tuple[float, float]) -> tuple[float, float]:
    if len(points_m) < 2:
        raise DemAcquisitionError("At least two upstream flowline vertices are required.")
    mean_x = sum(x for x, _ in points_m) / len(points_m)
    mean_y = sum(y for _, y in points_m) / len(points_m)
    sxx = sum((x - mean_x) ** 2 for x, _ in points_m)
    syy = sum((y - mean_y) ** 2 for _, y in points_m)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points_m)
    if abs(sxy) < 1.0e-12 and sxx >= syy:
        axis = (1.0, 0.0)
    elif abs(sxy) < 1.0e-12:
        axis = (0.0, 1.0)
    else:
        angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
        axis = (math.cos(angle), math.sin(angle))
    mean_vector = (mean_x - outlet_m[0], mean_y - outlet_m[1])
    if axis[0] * mean_vector[0] + axis[1] * mean_vector[1] < 0:
        axis = (-axis[0], -axis[1])
    return axis


def create_upstream_network_area(
    lon: float,
    lat: float,
    flowline_path: str | Path,
    output_path: str | Path,
    *,
    upstream_trace_distance_km: float = 40.0,
    upstream_margin_km: float = 5.0,
    downstream_margin_km: float = 3.0,
    lateral_margin_km: float = 4.0,
    envelope_type: str = "oriented_rectangle",
) -> DemAcquisitionArea:
    """Create a lightweight upstream-network DEM acquisition envelope from GeoJSON flowlines.

    This helper intentionally avoids heavy GIS dependencies. It expects reference
    flowlines as GeoJSON in EPSG:4326, collects vertices within
    ``upstream_trace_distance_km`` of the outlet, and builds either an oriented
    principal-axis rectangle or an axis-aligned envelope with safety margins.
    """

    if upstream_trace_distance_km <= 0 or lateral_margin_km <= 0:
        raise DemAcquisitionError("upstream_trace_distance_km and lateral_margin_km must be positive.")
    if upstream_margin_km < 0 or downstream_margin_km < 0:
        raise DemAcquisitionError("upstream_margin_km and downstream_margin_km cannot be negative.")
    flowline = Path(flowline_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    data = json.loads(flowline.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DemAcquisitionError(f"Expected GeoJSON flowlines: {flowline}")
    features = _geojson_features(data, flowline)
    outlet_m = (0.0, 0.0)
    max_distance_m = upstream_trace_distance_km * 1000.0
    local_points: list[tuple[float, float]] = []
    for feature in features:
        for point_lon, point_lat in _feature_points(feature):
            point_m = _lonlat_to_local_m(point_lon, point_lat, lon, lat)
            if math.hypot(point_m[0], point_m[1]) <= max_distance_m:
                local_points.append(point_m)
    if len(local_points) < 2:
        raise DemAcquisitionError("No flowline vertices found within upstream_trace_distance_km of the outlet.")
    local_points.append(outlet_m)
    margin_u = upstream_margin_km * 1000.0
    margin_d = downstream_margin_km * 1000.0
    margin_l = lateral_margin_km * 1000.0
    envelope = envelope_type.lower()
    if envelope == "axis_aligned_rectangle":
        xs = [x for x, _ in local_points]
        ys = [y for _, y in local_points]
        minx, maxx = min(xs) - margin_l, max(xs) + margin_l
        miny, maxy = min(ys) - margin_l, max(ys) + margin_l
        minx = min(minx, -margin_d)
        maxx = max(maxx, margin_u)
        coords_m = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
    elif envelope == "oriented_rectangle":
        axis = _principal_axis(local_points, outlet_m)
        lateral = (-axis[1], axis[0])
        projections = [x * axis[0] + y * axis[1] for x, y in local_points]
        lateral_offsets = [x * lateral[0] + y * lateral[1] for x, y in local_points]
        min_p = min(min(projections) - margin_d, -margin_d)
        max_p = max(max(projections) + margin_u, margin_u)
        min_l = min(lateral_offsets) - margin_l
        max_l = max(lateral_offsets) + margin_l
        corners = [(min_p, min_l), (min_p, max_l), (max_p, max_l), (max_p, min_l)]
        coords_m = [
            (p * axis[0] + offset * lateral[0], p * axis[1] + offset * lateral[1])
            for p, offset in corners
        ]
        coords_m.append(coords_m[0])
    else:
        raise DemAcquisitionError("envelope_type must be oriented_rectangle or axis_aligned_rectangle.")
    coords = [_local_m_to_lonlat(x, y, lon, lat) for x, y in coords_m]
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    area_km2 = _polygon_area_km2(coords, lat)
    _write_geojson_polygon(
        output,
        coords,
        {
            "mode": "upstream_network",
            "flowline_path": str(flowline),
            "outlet_lon": lon,
            "outlet_lat": lat,
            "upstream_trace_distance_km": upstream_trace_distance_km,
            "upstream_margin_km": upstream_margin_km,
            "downstream_margin_km": downstream_margin_km,
            "lateral_margin_km": lateral_margin_km,
            "envelope_type": envelope,
            "selected_vertex_count": len(local_points) - 1,
            "area_km2": area_km2,
        },
    )
    return DemAcquisitionArea("upstream_network", output, (min(xs), min(ys), max(xs), max(ys)), area_km2)


def _polygon_area_km2(coords: list[tuple[float, float]], origin_lat: float) -> float:
    if len(coords) < 4:
        return 0.0
    origin_lon = sum(x for x, _ in coords) / len(coords)
    local = [_lonlat_to_local_m(x, y, origin_lon, origin_lat) for x, y in coords]
    area = 0.0
    for (x1, y1), (x2, y2) in zip(local, local[1:]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2_000_000.0


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



def _geojson_features(data: dict[str, object], path: Path) -> list[dict[str, object]]:
    if data.get("type") == "Feature":
        return [data]
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list) or not features:
        raise DemAcquisitionError(f"Expected GeoJSON FeatureCollection with at least one feature: {path}")
    return [feature for feature in features if isinstance(feature, dict)]


def _bounds_from_points(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _feature_points(feature: dict[str, object]) -> list[tuple[float, float]]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise DemAcquisitionError("GeoJSON feature is missing geometry.")
    return _geometry_coords(geometry)


def _bounds_from_geojson_feature(feature: dict[str, object]) -> tuple[float, float, float, float]:
    return _bounds_from_points(_feature_points(feature))


def _polygon_rings(geometry: dict[str, object]) -> list[list[tuple[float, float]]]:
    gtype = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if gtype == "Polygon" and isinstance(coordinates, list):
        rings = coordinates
    elif gtype == "MultiPolygon" and isinstance(coordinates, list):
        rings = [ring for polygon in coordinates if isinstance(polygon, list) for ring in polygon]
    else:
        return []
    parsed: list[list[tuple[float, float]]] = []
    for ring in rings:
        if not isinstance(ring, list):
            continue
        points = []
        for coord in ring:
            if (
                isinstance(coord, list)
                and len(coord) >= 2
                and isinstance(coord[0], (int, float))
                and isinstance(coord[1], (int, float))
            ):
                points.append((float(coord[0]), float(coord[1])))
        if len(points) >= 3:
            parsed.append(points)
    return parsed


def _feature_polygons(feature: dict[str, object]) -> list[list[tuple[float, float]]]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return []
    return _polygon_rings(geometry)


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        crosses = (yi > y) != (yj > y)
        if crosses:
            x_at_y = (xj - xi) * (y - yi) / ((yj - yi) or 1.0e-12) + xi
            if x < x_at_y:
                inside = not inside
        j = i
    return inside


def _orientation(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, c) -> bool:
    return (
        min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
        and abs(_orientation(a, b, c)) < 1.0e-12
    )


def _segments_intersect(a, b, c, d) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    return (
        _on_segment(a, b, c)
        or _on_segment(a, b, d)
        or _on_segment(c, d, a)
        or _on_segment(c, d, b)
    )


def _ring_edges(ring: list[tuple[float, float]]):
    points = ring if ring[0] == ring[-1] else [*ring, ring[0]]
    return zip(points, points[1:])


def _rings_intersect(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    if any(_point_in_ring(point, b) for point in a):
        return True
    if any(_point_in_ring(point, a) for point in b):
        return True
    return any(_segments_intersect(a1, a2, b1, b2) for a1, a2 in _ring_edges(a) for b1, b2 in _ring_edges(b))


def _features_intersect(acquisition_features: list[dict[str, object]], tile_feature: dict[str, object]) -> bool:
    tile_polygons = _feature_polygons(tile_feature)
    acquisition_polygons = [
        polygon
        for feature in acquisition_features
        for polygon in _feature_polygons(feature)
    ]
    if acquisition_polygons and tile_polygons:
        return any(_rings_intersect(a, b) for a in acquisition_polygons for b in tile_polygons)
    tile_bounds = _bounds_from_geojson_feature(tile_feature)
    acquisition_points = [point for feature in acquisition_features for point in _feature_points(feature)]
    return _intersects(_bounds_from_points(acquisition_points), tile_bounds)

def _bounds_from_geojson(path: Path) -> tuple[float, float, float, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DemAcquisitionError(f"Expected GeoJSON object: {path}")
    features = _geojson_features(data, path)
    points = [point for feature in features for point in _feature_points(feature)]
    if not points:
        raise DemAcquisitionError(f"No geometry coordinates found in: {path}")
    return _bounds_from_points(points)


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
    acquisition_data = json.loads(acquisition_path.read_text(encoding="utf-8"))
    if not isinstance(acquisition_data, dict):
        raise DemAcquisitionError(f"Expected GeoJSON acquisition area: {acquisition_path}")
    acquisition_features = _geojson_features(acquisition_data, acquisition_path)
    acquisition_bounds = _bounds_from_geojson(acquisition_path)
    data = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DemAcquisitionError(f"Expected GeoJSON tile-index object: {index_path}")
    features = _geojson_features(data, index_path)

    items: list[dict[str, object]] = []
    tiles: list[str] = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            continue
        tile_bounds = _bounds_from_geojson_feature(feature)
        if not _intersects(acquisition_bounds, tile_bounds):
            continue
        if not _features_intersect(acquisition_features, feature):
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



@dataclass(frozen=True)
class DemBoundaryValidation:
    is_valid: bool
    touched_edges: tuple[str, ...]
    distances_m: dict[str, float]


def _meters_per_degree_at_lat(lat: float) -> tuple[float, float]:
    meters_per_lat = METERS_PER_DEGREE
    meters_per_lon = METERS_PER_DEGREE * max(0.1, abs(math.cos(math.radians(lat))))
    return meters_per_lon, meters_per_lat


def validate_watershed_within_acquisition(
    watershed_area: str | Path,
    acquisition_area: str | Path,
    *,
    safety_distance_m: float = 500.0,
) -> DemBoundaryValidation:
    """Check whether a delineated watershed is too close to the DEM acquisition edge.

    Inputs are GeoJSON in EPSG:4326. The check is intentionally bounds-based:
    once a delineated watershed exists, this answers the workflow-control question
    "which side should be expanded before trying again?" without requiring GIS
    dependencies in the terminal/UI orchestration layer.
    """

    watershed_bounds = _bounds_from_geojson(Path(watershed_area).expanduser().resolve())
    acquisition_bounds = _bounds_from_geojson(Path(acquisition_area).expanduser().resolve())
    minx, miny, maxx, maxy = watershed_bounds
    aminx, aminy, amaxx, amaxy = acquisition_bounds
    center_lat = (miny + maxy) / 2.0
    meters_per_lon, meters_per_lat = _meters_per_degree_at_lat(center_lat)
    distances = {
        "west": (minx - aminx) * meters_per_lon,
        "south": (miny - aminy) * meters_per_lat,
        "east": (amaxx - maxx) * meters_per_lon,
        "north": (amaxy - maxy) * meters_per_lat,
    }
    touched = tuple(edge for edge, distance in distances.items() if distance < safety_distance_m)
    return DemBoundaryValidation(not touched, touched, distances)


def expand_acquisition_bounds(
    acquisition_area: str | Path,
    output_path: str | Path,
    touched_edges: tuple[str, ...] | list[str],
    *,
    expansion_distance_km: float = 5.0,
) -> DemAcquisitionArea:
    """Write an expanded axis-aligned acquisition polygon in touched directions."""

    if expansion_distance_km <= 0:
        raise DemAcquisitionError("expansion_distance_km must be positive.")
    source = Path(acquisition_area).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    minx, miny, maxx, maxy = _bounds_from_geojson(source)
    center_lat = (miny + maxy) / 2.0
    lon_deg, lat_deg = _degrees_per_meter(center_lat)
    delta_lon = expansion_distance_km * 1000.0 * lon_deg
    delta_lat = expansion_distance_km * 1000.0 * lat_deg
    edges = set(touched_edges)
    valid_edges = {"west", "south", "east", "north"}
    invalid_edges = edges - valid_edges
    if invalid_edges:
        raise DemAcquisitionError(f"Invalid expansion edge(s): {', '.join(sorted(invalid_edges))}")
    if "west" in edges:
        minx -= delta_lon
    if "east" in edges:
        maxx += delta_lon
    if "south" in edges:
        miny -= delta_lat
    if "north" in edges:
        maxy += delta_lat
    coords = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]
    width_km = (maxx - minx) / lon_deg / 1000.0
    height_km = (maxy - miny) / lat_deg / 1000.0
    area_km2 = width_km * height_km
    _write_geojson_polygon(
        output,
        coords,
        {
            "mode": "directional_expansion",
            "source_area": str(source),
            "expanded_edges": sorted(edges),
            "expansion_distance_km": expansion_distance_km,
            "area_km2": area_km2,
        },
    )
    return DemAcquisitionArea("directional_expansion", output, (minx, miny, maxx, maxy), area_km2)

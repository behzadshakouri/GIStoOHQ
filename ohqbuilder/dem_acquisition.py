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

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .dem_materializer import bounds_from_lonlat_buffer

NLDI_BASE_URL = "https://api.water.usgs.gov/nldi/linked-data"


class WatershedBoundsError(RuntimeError):
    """Raised when web watershed bounds cannot be resolved."""


@dataclass(frozen=True)
class WatershedBoundsResult:
    bounds: tuple[float, float, float, float]
    source: str
    url: str | None = None


def _load_json(url: str, *, timeout: float = 20.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _iter_positions(geometry: Any):
    if not isinstance(geometry, dict):
        return
    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return

    def walk(value):
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            yield float(value[0]), float(value[1])
            return
        if isinstance(value, list):
            for child in value:
                yield from walk(child)

    yield from walk(coordinates)


def _feature_collection_bounds(collection: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for feature in collection.get("features", []):
        for x, y in _iter_positions(feature.get("geometry")) or []:
            xs.append(x)
            ys.append(y)
    if not xs or not ys:
        raise WatershedBoundsError("NLDI response did not include basin geometry coordinates.")
    return min(xs), min(ys), max(xs), max(ys)


def expand_bounds(
    bounds: tuple[float, float, float, float],
    *,
    scale: float = 1.1,
) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    if scale <= 1.0:
        return bounds
    pad_x = ((maxx - minx) * (scale - 1.0)) / 2.0
    pad_y = ((maxy - miny) * (scale - 1.0)) / 2.0
    return minx - pad_x, miny - pad_y, maxx + pad_x, maxy + pad_y


def resolve_nldi_basin_bounds(
    *,
    lon: float,
    lat: float,
    safety_scale: float = 1.1,
    timeout: float = 20.0,
) -> WatershedBoundsResult:
    """Resolve upstream basin bounds from the USGS NLDI web API."""

    coords = urllib.parse.quote(f"POINT({lon} {lat})")
    position_url = f"{NLDI_BASE_URL}/comid/position?f=json&coords={coords}"
    position = _load_json(position_url, timeout=timeout)
    features = position.get("features") or []
    if not features:
        raise WatershedBoundsError("NLDI did not return a COMID for the watershed coordinate.")
    properties = features[0].get("properties") or {}
    comid = properties.get("identifier") or properties.get("comid") or properties.get("COMID")
    if comid is None:
        raise WatershedBoundsError("NLDI COMID response did not include an identifier.")

    basin_url = f"{NLDI_BASE_URL}/comid/{comid}/basin?f=json&splitCatchment=true"
    basin = _load_json(basin_url, timeout=timeout)
    bounds = expand_bounds(_feature_collection_bounds(basin), scale=safety_scale)
    return WatershedBoundsResult(bounds=bounds, source="nldi", url=basin_url)


def resolve_materialization_bounds(
    *,
    lon: float,
    lat: float,
    buffer_m: float,
    safety_scale: float = 1.1,
    prefer_web: bool = True,
    timeout: float = 20.0,
) -> WatershedBoundsResult:
    """Resolve web watershed bounds, falling back to coordinate-buffer bounds."""

    if prefer_web:
        try:
            return resolve_nldi_basin_bounds(
                lon=lon,
                lat=lat,
                safety_scale=safety_scale,
                timeout=timeout,
            )
        except Exception:
            pass
    return WatershedBoundsResult(
        bounds=bounds_from_lonlat_buffer(lon, lat, buffer_m, scale=safety_scale),
        source="coordinate-buffer",
        url=None,
    )

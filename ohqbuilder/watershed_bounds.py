from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .dem_materializer import bounds_from_lonlat_buffer

NLDI_BASE_URL = "https://api.water.usgs.gov/nldi/linked-data"

# Adaptive padding defaults. The larger of these two margins is used.
DEFAULT_MARGIN_FRACTION = 0.05
DEFAULT_MINIMUM_MARGIN_M = 250.0


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


def _feature_collection_bounds(
    collection: dict[str, Any],
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    for feature in collection.get("features", []):
        for x, y in _iter_positions(feature.get("geometry")) or []:
            xs.append(x)
            ys.append(y)

    if not xs or not ys:
        raise WatershedBoundsError(
            "NLDI response did not include basin geometry coordinates."
        )

    return min(xs), min(ys), max(xs), max(ys)


def _meters_per_degree(latitude: float) -> tuple[float, float]:
    """Return approximate meters per degree of longitude and latitude."""

    lat_rad = math.radians(latitude)

    meters_per_degree_lat = (
        111132.92
        - 559.82 * math.cos(2.0 * lat_rad)
        + 1.175 * math.cos(4.0 * lat_rad)
        - 0.0023 * math.cos(6.0 * lat_rad)
    )

    meters_per_degree_lon = (
        111412.84 * math.cos(lat_rad)
        - 93.5 * math.cos(3.0 * lat_rad)
        + 0.118 * math.cos(5.0 * lat_rad)
    )

    return max(meters_per_degree_lon, 1.0), max(meters_per_degree_lat, 1.0)


def expand_bounds(
    bounds: tuple[float, float, float, float],
    *,
    margin_fraction: float = DEFAULT_MARGIN_FRACTION,
    minimum_margin_m: float = DEFAULT_MINIMUM_MARGIN_M,
    scale: float | None = None,
) -> tuple[float, float, float, float]:
    """Expand geographic bounds with an adaptive, uniform physical margin.

    The applied margin is the larger of:

    * ``margin_fraction`` times the larger watershed dimension; and
    * ``minimum_margin_m``.

    This replaces the old fixed 1.2 bounding-box scale and provides a more
    consistent physical margin for elongated and irregular watersheds.
    """

    minx, miny, maxx, maxy = bounds

    if scale is not None:
        if scale < 0.0:
            raise ValueError("scale must be greater than or equal to zero.")
        if scale == 1.0:
            return bounds
        center_x = (minx + maxx) / 2.0
        center_y = (miny + maxy) / 2.0
        half_width = (maxx - minx) * scale / 2.0
        half_height = (maxy - miny) * scale / 2.0
        return (
            center_x - half_width,
            center_y - half_height,
            center_x + half_width,
            center_y + half_height,
        )

    if minx > maxx or miny > maxy:
        raise WatershedBoundsError(f"Invalid bounds ordering: {bounds!r}")

    if margin_fraction < 0.0:
        raise ValueError("margin_fraction must be greater than or equal to zero.")

    if minimum_margin_m < 0.0:
        raise ValueError("minimum_margin_m must be greater than or equal to zero.")

    center_lat = (miny + maxy) / 2.0
    meters_per_degree_lon, meters_per_degree_lat = _meters_per_degree(center_lat)

    width_m = (maxx - minx) * meters_per_degree_lon
    height_m = (maxy - miny) * meters_per_degree_lat
    characteristic_size_m = max(width_m, height_m)

    margin_m = max(
        minimum_margin_m,
        margin_fraction * characteristic_size_m,
    )

    pad_lon = margin_m / meters_per_degree_lon
    pad_lat = margin_m / meters_per_degree_lat

    return (
        minx - pad_lon,
        miny - pad_lat,
        maxx + pad_lon,
        maxy + pad_lat,
    )


def resolve_nldi_basin_bounds(
    *,
    lon: float,
    lat: float,
    margin_fraction: float = DEFAULT_MARGIN_FRACTION,
    minimum_margin_m: float = DEFAULT_MINIMUM_MARGIN_M,
    safety_scale: float | None = None,
    timeout: float = 20.0,
) -> WatershedBoundsResult:
    """Resolve upstream basin bounds from the USGS NLDI web API."""

    coords = urllib.parse.quote(f"POINT({lon} {lat})")
    position_url = f"{NLDI_BASE_URL}/comid/position?f=json&coords={coords}"

    position = _load_json(position_url, timeout=timeout)
    features = position.get("features") or []

    if not features:
        raise WatershedBoundsError(
            "NLDI did not return a COMID for the watershed coordinate."
        )

    properties = features[0].get("properties") or {}
    comid = (
        properties.get("identifier")
        or properties.get("comid")
        or properties.get("COMID")
    )

    if comid is None:
        raise WatershedBoundsError(
            "NLDI COMID response did not include an identifier."
        )

    basin_url = (
        f"{NLDI_BASE_URL}/comid/{comid}/basin"
        "?f=json&splitCatchment=true"
    )
    basin = _load_json(basin_url, timeout=timeout)

    raw_bounds = _feature_collection_bounds(basin)
    bounds = expand_bounds(
        raw_bounds,
        margin_fraction=margin_fraction,
        minimum_margin_m=minimum_margin_m,
        scale=safety_scale,
    )

    return WatershedBoundsResult(
        bounds=bounds,
        source="nldi",
        url=basin_url,
    )


def resolve_materialization_bounds(
    *,
    lon: float,
    lat: float,
    buffer_m: float,
    margin_fraction: float = DEFAULT_MARGIN_FRACTION,
    minimum_margin_m: float = DEFAULT_MINIMUM_MARGIN_M,
    prefer_web: bool = True,
    timeout: float = 20.0,
) -> WatershedBoundsResult:
    """Resolve watershed materialization bounds.

    NLDI basin geometry is preferred. If the web lookup fails, this function
    falls back to a coordinate-centered buffer and applies the same adaptive
    physical margin. The fallback's old fixed-scale expansion is disabled by
    passing ``scale=1.0`` to ``bounds_from_lonlat_buffer``.
    """

    if prefer_web:
        try:
            return resolve_nldi_basin_bounds(
                lon=lon,
                lat=lat,
                margin_fraction=margin_fraction,
                minimum_margin_m=minimum_margin_m,
                timeout=timeout,
            )
        except Exception:
            # Preserve the existing resilient behavior: an NLDI/network failure
            # must not prevent local materialization from continuing.
            pass

    fallback_bounds = bounds_from_lonlat_buffer(
        lon,
        lat,
        buffer_m,
        scale=1.0,
    )
    bounds = expand_bounds(
        fallback_bounds,
        margin_fraction=margin_fraction,
        minimum_margin_m=minimum_margin_m,
    )

    return WatershedBoundsResult(
        bounds=bounds,
        source="coordinate-buffer",
        url=None,
    )

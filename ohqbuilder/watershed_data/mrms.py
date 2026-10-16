from __future__ import annotations

import gzip
import io
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .catalog import AssetCatalog, ObjectStore
from .network import download_bytes
from .schemas import SiteSpec, WatershedDataError, canonical_request_key

IEM_ARCHIVE_BASE = "https://mtarchive.geol.iastate.edu"
# MRMS renamed its gauge-corrected hourly QPE product partway through its
# history - confirmed during the NSE>=0.8 recovery program's forcing audit
# (results/nse08_recovery_20261015/phase1_forcing_audit/05_precip_comparison_all_10_floods.md):
# GaugeCorr_QPE_01H is present through at least 2020-05-29 and absent by
# 2021-08-09 (404s); MultiSensor_QPE_01H_Pass2 is present from at least
# 2021-08-09 onward. A first-pass acquisition that only tried one name
# silently returned 404 pages that looked like (but were not) zero
# rainfall for every 2021-2022 hour - the exact failure mode this
# fallback list exists to prevent. Try oldest-first since most of this
# investigation's historical record predates the rename.
MRMS_PRODUCT_NAMES = ("GaugeCorr_QPE_01H", "MultiSensor_QPE_01H_Pass2", "MultiSensor_QPE_01H_Pass1")
GRID_CELL_DEG = 0.01  # MRMS CONUS grid native resolution, degrees
MIN_REAL_GRID_BYTES = 100_000  # a real decompressed CONUS grid is tens of MB; an IEM 404 page is <1KB
MISSING_SENTINEL_THRESHOLD = -0.01  # MRMS encodes missing/no-coverage as a negative sentinel


def _hourly_grib_url(product: str, dt_utc: datetime) -> tuple[str, str]:
    fname = f"{product}_00.00_{dt_utc:%Y%m%d}-{dt_utc:%H}0000.grib2"
    return f"{IEM_ARCHIVE_BASE}/{dt_utc:%Y/%m/%d}/mrms/ncep/{product}/{fname}.gz", fname


def download_mrms_hour(
    dt_utc: datetime,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    product_names: tuple[str, ...] = MRMS_PRODUCT_NAMES,
) -> tuple[bytes, str, str]:
    """Download and decompress one hour of MRMS gauge-corrected QPE.

    Returns (decompressed_grib2_bytes, product_name_used, source_url).
    Tries each name in `product_names` in turn - a 404 on the "wrong"
    name for a given date is expected (the product was renamed partway
    through MRMS's history), not a transient failure, so each attempt
    uses a single try (no blind retry-on-404)."""
    failures: list[str] = []
    for product in product_names:
        url, _ = _hourly_grib_url(product, dt_utc)
        try:
            gz_bytes, _, _ = download_bytes(
                url, opener=opener, timeout=20.0, attempts=1,
                label=f"MRMS {product} hourly QPE acquisition",
            )
        except WatershedDataError as exc:
            failures.append(str(exc))
            continue
        if len(gz_bytes) < 1000:
            failures.append(f"{product}: response too small to be real archive data ({len(gz_bytes)} bytes)")
            continue
        try:
            raw = gzip.decompress(gz_bytes)
        except OSError as exc:
            failures.append(f"{product}: gzip decompression failed: {exc}")
            continue
        if len(raw) < MIN_REAL_GRID_BYTES:
            failures.append(f"{product}: decompressed grid too small ({len(raw)} bytes) - likely an error page")
            continue
        return raw, product, url
    raise WatershedDataError(
        f"MRMS hourly QPE unavailable for {dt_utc.isoformat()} under every known product name: "
        + "; ".join(failures)
    )


def area_weighted_basin_average(
    cell_lats: list[float],
    cell_lons_180: list[float],
    cell_values: list[float],
    cell_weights_m2: list[float],
    *,
    missing_sentinel_threshold: float = MISSING_SENTINEL_THRESHOLD,
) -> dict[str, Any]:
    """Pure area-weighted basin average over pre-computed cell/basin
    intersection weights (m^2) - no GRIB or network dependency, so this
    is unit-testable with synthetic inputs.

    `cell_weights_m2[k]` must already be each cell's intersection area
    with the basin polygon (computed in an equal-area CRS by the
    caller, e.g. EPSG:5070) - zero for any cell outside the basin.
    `cell_values` are the grid's native values (e.g. mm for an hourly
    QPE accumulation); values below `missing_sentinel_threshold` are
    MRMS's no-coverage/missing encoding and excluded, not treated as
    legitimate negative precipitation."""
    if not (len(cell_lats) == len(cell_lons_180) == len(cell_values) == len(cell_weights_m2)):
        raise WatershedDataError("cell_lats/cell_lons_180/cell_values/cell_weights_m2 must be the same length")
    total_weight = 0.0
    weighted_sum = 0.0
    used_values: list[float] = []
    used_weights: list[float] = []
    n_missing_nonfinite = 0
    n_negative_sentinel = 0
    n_zero_weight = 0
    for value, weight in zip(cell_values, cell_weights_m2):
        if weight <= 0.0:
            n_zero_weight += 1
            continue
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf check, no numpy dependency
            n_missing_nonfinite += 1
            continue
        if value < missing_sentinel_threshold:
            n_negative_sentinel += 1
            continue
        total_weight += weight
        weighted_sum += weight * value
        used_values.append(value)
        used_weights.append(weight)

    if total_weight <= 0.0:
        return {
            "mean": None, "n_cells_used": 0, "weighted_area_m2": 0.0,
            "n_missing_nonfinite": n_missing_nonfinite, "n_negative_sentinel": n_negative_sentinel,
            "n_zero_weight": n_zero_weight, "cell_min": None, "cell_max": None,
            "cell_std": None, "cell_cv": None,
        }
    mean_value = weighted_sum / total_weight
    variance = sum(w * (v - mean_value) ** 2 for v, w in zip(used_values, used_weights)) / total_weight
    std = variance ** 0.5
    return {
        "mean": mean_value, "n_cells_used": len(used_values), "weighted_area_m2": total_weight,
        "n_missing_nonfinite": n_missing_nonfinite, "n_negative_sentinel": n_negative_sentinel,
        "n_zero_weight": n_zero_weight, "cell_min": min(used_values), "cell_max": max(used_values),
        "cell_std": std, "cell_cv": (std / mean_value) if mean_value > 1e-9 else None,
    }


def basin_cell_intersection_weights_m2(
    basin_geometry_wgs84: Any,
    grid_lats: list[float],
    grid_lons_360: list[float],
    *,
    cell_deg: float = GRID_CELL_DEG,
    pad_cells: int = 2,
) -> tuple[list[float], list[float], list[float]]:
    """Returns (cell_lats, cell_lons_180, weights_m2) for every grid cell
    whose `cell_deg`-sized box (centered on each lat/lon pair) intersects
    `basin_geometry_wgs84` - MRMS longitudes are in 0-360 convention.
    Requires shapely+pyproj (optional `gis` extra); callers on a plain
    install should catch ImportError and skip MRMS acquisition."""
    from shapely.geometry import box
    from shapely.ops import transform
    import pyproj

    to_albers = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    basin_albers = transform(to_albers, basin_geometry_wgs84)
    lon_min, lat_min, lon_max, lat_max = basin_geometry_wgs84.bounds

    out_lats: list[float] = []
    out_lons: list[float] = []
    out_weights: list[float] = []
    for lat in grid_lats:
        if lat < lat_min - pad_cells * cell_deg or lat > lat_max + pad_cells * cell_deg:
            continue
        for lon_360 in grid_lons_360:
            lon_180 = lon_360 - 360.0
            if lon_180 < lon_min - pad_cells * cell_deg or lon_180 > lon_max + pad_cells * cell_deg:
                continue
            cell = box(lon_180 - cell_deg / 2, lat - cell_deg / 2, lon_180 + cell_deg / 2, lat + cell_deg / 2)
            cell_albers = transform(to_albers, cell)
            inter = cell_albers.intersection(basin_albers)
            out_lats.append(lat)
            out_lons.append(lon_180)
            out_weights.append(inter.area if not inter.is_empty else 0.0)
    return out_lats, out_lons, out_weights


def acquire_basin_precipitation(
    spec: SiteSpec,
    basin_geometry_geojson: dict[str, Any],
    *,
    start_utc: datetime,
    end_utc: datetime,
    cache: str | Path,
    catalog: str | Path,
    basin_geometry_source: str = "usgs-nldi",
    opener: Callable[..., object] = urllib.request.urlopen,
    refresh: bool = False,
) -> dict[str, Any]:
    """Acquire basin-averaged hourly MRMS precipitation for [start_utc,
    end_utc] (inclusive, hourly steps) and register it with full
    provenance in the asset catalog - mirrors acquire_historical_meteorology's
    NASA POWER pattern in nasa_power.py but with real area-weighted
    basin averaging instead of a single provider point value
    (spatial_support="basin_area_weighted" vs. NASA POWER's
    "provider_point").

    Requires shapely, pyproj, xarray, and cfgrib (cfgrib additionally
    needs the system eccodes library) - all optional dependencies; the
    ImportError from a missing one propagates with its own message
    rather than being caught here, since there is no partial/degraded
    mode for this acquisition that would be safe to silently fall back
    to."""
    import xarray as xr  # noqa: F401 (import-checked here so the error surfaces before any download)
    from shapely.geometry import shape

    basin_geometry = shape(basin_geometry_geojson["features"][0]["geometry"]
                            if "features" in basin_geometry_geojson else basin_geometry_geojson)

    request_parameters = {
        "basin_bounds": ",".join(f"{v:.6f}" for v in basin_geometry.bounds),
        "start": start_utc.strftime("%Y%m%dT%H"), "end": end_utc.strftime("%Y%m%dT%H"),
    }
    request_key = canonical_request_key("mrms-iem-archive", "GaugeCorr_QPE_01H/MultiSensor_QPE_01H_Pass2",
                                         request_parameters, "hourly-basin-average-v1")
    catalog_store = AssetCatalog(catalog)
    if not refresh and (cached := catalog_store.cached_request(request_key, cache)) is not None:
        return cached

    hours = int((end_utc - start_utc).total_seconds() / 3600) + 1
    rows: list[dict[str, Any]] = []
    products_used: dict[str, int] = {}
    n_hours_failed = 0
    acquisition_attempts = 0
    cell_weights_cache: tuple[list[float], list[float], list[float]] | None = None

    for i in range(hours):
        dt = start_utc + timedelta(hours=i)
        acquisition_attempts += 1
        try:
            raw, product, source_url = download_mrms_hour(dt, opener=opener)
        except WatershedDataError:
            n_hours_failed += 1
            continue
        products_used[product] = products_used.get(product, 0) + 1

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
            tmp.write(raw)
            tmp.flush()
            ds = xr.open_dataset(tmp.name, engine="cfgrib", backend_kwargs={"indexpath": ""})
            varname = list(ds.data_vars)[0]
            da = ds[varname]
            if cell_weights_cache is None:
                # Computed once (first hour only): basin bounding-box intersection
                # weights, AND the integer row/col positions of those same cells
                # within the CONUS grid - cached together so every subsequent hour
                # does plain numpy positional indexing (fast) instead of xarray's
                # coordinate-value .sel() (confirmed ~129s/hour in a timed 24h
                # run - a per-cell .sel() call does a coordinate-index search
                # against the full 7000-wide CONUS longitude axis every time;
                # positional indexing into a pre-resolved integer position is the
                # fix, not a new network/decode cost).
                lats = [float(v) for v in da.latitude.values]
                lons_360 = [float(v) for v in da.longitude.values]
                weights_result = basin_cell_intersection_weights_m2(basin_geometry, lats, lons_360)
                lat_index = {v: i for i, v in enumerate(lats)}
                lon_index = {v: i for i, v in enumerate(lons_360)}
                positions = [(lat_index[lat], lon_index[lon + 360.0]) for lat, lon in zip(weights_result[0], weights_result[1])]
                cell_weights_cache = (*weights_result, positions)
            cell_lats, cell_lons, weights, positions = cell_weights_cache
            grid = da.values
            values = [float(grid[i, j]) for i, j in positions]
        stats = area_weighted_basin_average(cell_lats, cell_lons, values, weights)
        rows.append({
            "timestamp_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "value": stats["mean"], "n_cells_used": stats["n_cells_used"],
            "n_negative_sentinel": stats["n_negative_sentinel"], "cell_cv": stats["cell_cv"],
            "product": product,
        })

    if not rows:
        raise WatershedDataError(f"MRMS basin acquisition produced zero valid hours out of {hours} requested")

    payload = json.dumps({"rows": rows}, sort_keys=True).encode("utf-8")
    stored = ObjectStore(cache).put(io.BytesIO(payload))
    summary = {
        "variables": ["PRECIP_BASIN_AVG"], "native_units": {"PRECIP_BASIN_AVG": "mm/hour"},
        "temporal_resolution": "hourly", "time_standard": "UTC",
        "temporal_coverage": {"start": rows[0]["timestamp_utc"], "end": rows[-1]["timestamp_utc"]},
        "observation_counts": {"PRECIP_BASIN_AVG": len(rows)},
        "n_hours_requested": hours, "n_hours_failed": n_hours_failed,
        "spatial_support": "basin_area_weighted",
        "basin_geometry_source": basin_geometry_source,
        "basin_geometry_bounds": list(basin_geometry.bounds),
        "products_used": products_used,
    }
    return catalog_store.register({
        "provider": "mrms-iem-archive", "product": "hourly-basin-average",
        "product_version": "hourly-basin-average-v1", "request_parameters": request_parameters,
        "request_key": request_key,
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "source_url": IEM_ARCHIVE_BASE,
        "processing_status": "basin-area-weighted", "longitude": spec.longitude,
        "latitude": spec.latitude, "variable_semantics": "meteorological_forcing",
        "acquisition_attempts": acquisition_attempts, **summary,
    })

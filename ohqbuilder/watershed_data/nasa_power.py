from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .catalog import AssetCatalog, ObjectStore
from .schemas import SiteSpec, WatershedDataError, canonical_request_key

POWER_HOURLY_POINT = "https://power.larc.nasa.gov/api/temporal/hourly/point"
POWER_DAILY_POINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
DEFAULT_PARAMETERS = ("PRECTOTCORR", "T2M", "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN")
DEFAULT_PET_PARAMETERS = ("EVPTRNS",)


def build_meteorology_query(
    spec: SiteSpec, parameters: tuple[str, ...] = DEFAULT_PARAMETERS, *, temporal: str = "hourly"
) -> tuple[str, dict[str, str]]:
    if not parameters or any(not value.replace("_", "").isalnum() for value in parameters):
        raise WatershedDataError("NASA POWER parameters must be non-empty variable codes")
    if temporal not in {"hourly", "daily"}:
        raise WatershedDataError("NASA POWER temporal resolution must be hourly or daily")
    endpoint = POWER_HOURLY_POINT if temporal == "hourly" else POWER_DAILY_POINT
    return endpoint, {
        "parameters": ",".join(parameters), "community": "AG",
        "longitude": str(spec.longitude), "latitude": str(spec.latitude),
        "start": spec.study_start[:10].replace("-", ""),
        "end": spec.study_end[:10].replace("-", ""), "format": "JSON",
        "time-standard": "UTC",
    }


def summarize_meteorology_json(
    raw: bytes, requested: tuple[str, ...], *, temporal: str = "hourly"
) -> dict[str, object]:
    try:
        document = json.loads(raw)
        parameter_data = document["properties"]["parameter"]
        parameter_units = document["parameters"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise WatershedDataError(f"NASA POWER response is not valid {temporal} point JSON") from exc
    missing = sorted(set(requested) - set(parameter_data))
    if missing:
        raise WatershedDataError("NASA POWER response is missing variables: " + ", ".join(missing))
    timestamps = sorted({timestamp for code in requested for timestamp in parameter_data[code]})
    if not timestamps:
        raise WatershedDataError(f"NASA POWER response has no {temporal} observations")
    units = {
        code: str((parameter_units.get(code) or {}).get("units") or "unknown")
        for code in requested
    }
    missing_counts = {
        code: sum(value in (-999, -999.0, None) for value in parameter_data[code].values())
        for code in requested
    }
    return {
        "variables": list(requested), "native_units": units,
        "temporal_resolution": temporal, "time_standard": "UTC",
        "temporal_coverage": {"start": timestamps[0], "end": timestamps[-1]},
        "observation_counts": {code: len(parameter_data[code]) for code in requested},
        "missing_value_counts": missing_counts, "spatial_support": "provider_point",
    }


def acquire_historical_meteorology(
    spec: SiteSpec,
    *,
    cache: str | Path,
    catalog: str | Path,
    parameters: tuple[str, ...] = DEFAULT_PARAMETERS,
    opener: Callable[..., object] = urllib.request.urlopen,
    product: str = "historical-meteorology",
    semantics: str = "meteorological_forcing",
    temporal: str = "hourly",
    refresh: bool = False,
) -> dict[str, object]:
    endpoint, request_parameters = build_meteorology_query(spec, parameters, temporal=temporal)
    request_key = canonical_request_key(
        "nasa-power", endpoint, request_parameters, f"{temporal}-point-v1"
    )
    catalog_store = AssetCatalog(catalog)
    if not refresh and (cached := catalog_store.cached_request(request_key, cache)) is not None:
        return cached
    url = endpoint + "?" + urllib.parse.urlencode(request_parameters)
    try:
        with opener(url, timeout=120.0) as response:
            raw = response.read()
    except OSError as exc:
        raise WatershedDataError(f"NASA POWER meteorology acquisition failed: {exc}") from exc
    summary = summarize_meteorology_json(raw, parameters, temporal=temporal)
    stored = ObjectStore(cache).put(io.BytesIO(raw))
    return catalog_store.register({
        "provider": "nasa-power", "product": product,
        "product_version": f"{temporal}-point-v1", "request_parameters": request_parameters,
        "request_key": request_key,
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "source_url": url,
        "processing_status": "native", "longitude": spec.longitude,
        "latitude": spec.latitude, "variable_semantics": semantics, **summary,
    })


def acquire_pet_et(
    spec: SiteSpec, *, cache: str | Path, catalog: str | Path,
    parameters: tuple[str, ...] = DEFAULT_PET_PARAMETERS,
    opener: Callable[..., object] = urllib.request.urlopen,
    refresh: bool = False,
) -> dict[str, object]:
    return acquire_historical_meteorology(
        spec, cache=cache, catalog=catalog, parameters=parameters, opener=opener,
        product="pet-et", semantics="provider_evapotranspiration_parameter", temporal="daily",
        refresh=refresh,
    )

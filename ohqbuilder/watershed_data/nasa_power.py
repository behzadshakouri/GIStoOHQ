from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .catalog import AssetCatalog, ObjectStore
from .network import download_bytes
from .schemas import SiteSpec, WatershedDataError, canonical_request_key

POWER_HOURLY_POINT = "https://power.larc.nasa.gov/api/temporal/hourly/point"
POWER_DAILY_POINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
DEFAULT_PARAMETERS = ("PRECTOTCORR", "T2M", "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN")
DEFAULT_PET_PARAMETERS = ("EVPTRNS",)
# The hourly point JSON endpoint rejects a request once (date span x parameter
# count) exceeds an undocumented size limit (HTTP 422, "please shorten your
# requested time extent for a JSON formatted data request" - confirmed against
# the live API: 5 parameters over ~6.5 years succeeds, over ~7 years fails).
# 1095 days (3 years) per chunk keeps well clear of that boundary regardless
# of how many parameters a caller requests.
MAX_HOURLY_REQUEST_DAYS = 1095


def _split_date_range(start: str, end: str, max_days: int) -> list[tuple[str, str]]:
    """Split a YYYYMMDD [start, end] span into contiguous <=max_days chunks."""
    cursor = date(int(start[0:4]), int(start[4:6]), int(start[6:8]))
    stop = date(int(end[0:4]), int(end[4:6]), int(end[6:8]))
    if cursor > stop:
        raise WatershedDataError(f"date range start {start} is after end {end}")
    chunks: list[tuple[str, str]] = []
    while cursor <= stop:
        chunk_end = min(cursor + timedelta(days=max_days - 1), stop)
        chunks.append((cursor.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _merge_power_documents(documents: list[dict]) -> dict:
    """Merge chunked POWER hourly responses into one document with the same
    schema a single (in-range) call would have produced."""
    merged = documents[0]
    parameter_data = merged["properties"]["parameter"]
    for document in documents[1:]:
        for code, series in document["properties"]["parameter"].items():
            parameter_data.setdefault(code, {}).update(series)
    return merged


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
    date_chunks = (
        _split_date_range(request_parameters["start"], request_parameters["end"], MAX_HOURLY_REQUEST_DAYS)
        if temporal == "hourly" else [(request_parameters["start"], request_parameters["end"])]
    )
    if len(date_chunks) <= 1:
        # Exact previous behavior for any range that fits in one request: a
        # single call, with the native provider bytes stored unmodified.
        url = endpoint + "?" + urllib.parse.urlencode(request_parameters)
        raw, _, acquisition_attempts = download_bytes(
            url, opener=opener, timeout=120.0, label="NASA POWER meteorology acquisition"
        )
        source_url = url
    else:
        documents = []
        acquisition_attempts = 0
        for chunk_start, chunk_end in date_chunks:
            chunk_parameters = dict(request_parameters, start=chunk_start, end=chunk_end)
            chunk_url = endpoint + "?" + urllib.parse.urlencode(chunk_parameters)
            chunk_raw, _, attempts = download_bytes(
                chunk_url, opener=opener, timeout=120.0,
                label=f"NASA POWER meteorology acquisition ({chunk_start}-{chunk_end})",
            )
            acquisition_attempts += attempts
            try:
                documents.append(json.loads(chunk_raw))
            except json.JSONDecodeError as exc:
                raise WatershedDataError(
                    f"NASA POWER response chunk {chunk_start}-{chunk_end} is not valid JSON"
                ) from exc
        raw = json.dumps(_merge_power_documents(documents)).encode("utf-8")
        source_url = f"{endpoint} (merged from {len(date_chunks)} chunked requests)"
    summary = summarize_meteorology_json(raw, parameters, temporal=temporal)
    stored = ObjectStore(cache).put(io.BytesIO(raw))
    return catalog_store.register({
        "provider": "nasa-power", "product": product,
        "product_version": f"{temporal}-point-v1", "request_parameters": request_parameters,
        "request_key": request_key,
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "source_url": source_url,
        "processing_status": "native", "longitude": spec.longitude,
        "latitude": spec.latitude, "variable_semantics": semantics,
        "acquisition_attempts": acquisition_attempts, **summary,
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

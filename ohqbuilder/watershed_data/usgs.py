from __future__ import annotations

import csv
import io
import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .schemas import SiteSpec, WatershedDataError
from .catalog import AssetCatalog, ObjectStore
from .network import download_bytes
from .schemas import canonical_request_key

USGS_SITE_SERVICE = "https://waterservices.usgs.gov/nwis/site/"
USGS_INSTANTANEOUS_VALUES_SERVICE = "https://waterservices.usgs.gov/nwis/iv/"
USGS_SERIES_CATALOG_LIMIT = 25


@dataclass(frozen=True)
class GaugeCandidate:
    provider: str
    station_id: str
    name: str
    longitude: float
    latitude: float
    distance_km: float
    drainage_area_km2: float | None
    record_start: str | None
    record_end: str | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _bounding_box(spec: SiteSpec, radius_km: float) -> str:
    if not math.isfinite(radius_km) or radius_km <= 0:
        raise WatershedDataError("USGS gauge discovery radius must be positive and finite")
    latitude_delta = radius_km / 111.32
    longitude_delta = radius_km / (111.32 * max(0.1, math.cos(math.radians(spec.latitude))))
    # NWIS rejects bBox coordinates with excessive decimal precision. Six places
    # retain sub-metre precision while satisfying the service request contract.
    return ",".join(f"{value:.6f}" for value in (
        spec.longitude - longitude_delta, spec.latitude - latitude_delta,
        spec.longitude + longitude_delta, spec.latitude + latitude_delta,
    ))


def build_site_query(spec: SiteSpec, radius_km: float) -> str:
    parameters = {
        "format": "rdb", "bBox": _bounding_box(spec, radius_km),
        "parameterCd": "00060", "siteStatus": "all", "siteOutput": "expanded",
    }
    return USGS_SITE_SERVICE + "?" + urllib.parse.urlencode(parameters)


def build_series_catalog_query(station_ids: list[str]) -> str:
    """Build a bounded follow-up query for station record coverage metadata."""
    if not station_ids or len(station_ids) > USGS_SERIES_CATALOG_LIMIT:
        raise WatershedDataError(
            f"USGS series catalog query requires 1-{USGS_SERIES_CATALOG_LIMIT} stations"
        )
    if any(not station_id.isdigit() for station_id in station_ids):
        raise WatershedDataError("USGS station IDs must contain digits only")
    parameters = {
        "format": "rdb", "sites": ",".join(station_ids), "parameterCd": "00060",
        "siteStatus": "all", "seriesCatalogOutput": "true",
    }
    return USGS_SITE_SERVICE + "?" + urllib.parse.urlencode(parameters)


def parse_site_rdb(text: str, spec: SiteSpec) -> list[GaugeCandidate]:
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 2:
        return []
    reader = csv.DictReader(io.StringIO("\n".join([lines[0], *lines[2:]])), delimiter="\t")
    candidates: dict[str, GaugeCandidate] = {}
    for row in reader:
        if not row.get("site_no") or not row.get("dec_long_va") or not row.get("dec_lat_va"):
            continue
        try:
            longitude, latitude = float(row["dec_long_va"]), float(row["dec_lat_va"])
        except ValueError:
            continue
        area = None
        try:
            if row.get("drain_area_va"):
                area = float(row["drain_area_va"]) * 2.589988110336
        except ValueError:
            pass
        candidate = GaugeCandidate(
            provider="usgs", station_id=row["site_no"], name=row.get("station_nm") or "",
            longitude=longitude, latitude=latitude,
            distance_km=_haversine_km(spec.longitude, spec.latitude, longitude, latitude),
            drainage_area_km2=area, record_start=row.get("begin_date") or None,
            record_end=row.get("end_date") or None,
            status=(row.get("site_status") or row.get("site_tp_cd") or "unknown").lower(),
        )
        previous = candidates.get(candidate.station_id)
        if previous is None:
            candidates[candidate.station_id] = candidate
            continue
        starts = [value for value in (previous.record_start, candidate.record_start) if value]
        ends = [value for value in (previous.record_end, candidate.record_end) if value]
        candidates[candidate.station_id] = GaugeCandidate(
            provider=previous.provider, station_id=previous.station_id,
            name=previous.name or candidate.name, longitude=previous.longitude,
            latitude=previous.latitude, distance_km=previous.distance_km,
            drainage_area_km2=(
                previous.drainage_area_km2
                if previous.drainage_area_km2 is not None else candidate.drainage_area_km2
            ),
            record_start=min(starts) if starts else None,
            record_end=max(ends) if ends else None,
            status=(
                "active" if "active" in {previous.status, candidate.status}
                else previous.status
            ),
        )
    return sorted(candidates.values(), key=lambda item: (item.distance_km, item.station_id))


def discover_gauges(
    spec: SiteSpec,
    *,
    radius_km: float = 50.0,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> tuple[str, list[GaugeCandidate]]:
    url = build_site_query(spec, radius_km)
    raw, _, _ = download_bytes(
        url, opener=opener, timeout=60.0, label="USGS gauge discovery"
    )
    candidates = parse_site_rdb(raw.decode("utf-8"), spec)
    nearest_ids = [
        candidate.station_id for candidate in candidates[:USGS_SERIES_CATALOG_LIMIT]
    ]
    if not nearest_ids:
        return url, candidates
    coverage_url = build_series_catalog_query(nearest_ids)
    coverage_raw, _, _ = download_bytes(
        coverage_url, opener=opener, timeout=60.0, label="USGS gauge record coverage"
    )
    coverage = {
        candidate.station_id: candidate
        for candidate in parse_site_rdb(coverage_raw.decode("utf-8"), spec)
    }
    enriched = [
        replace(
            candidate,
            drainage_area_km2=(
                candidate.drainage_area_km2
                if candidate.drainage_area_km2 is not None
                else coverage[candidate.station_id].drainage_area_km2
            ),
            record_start=coverage[candidate.station_id].record_start,
            record_end=coverage[candidate.station_id].record_end,
            status=coverage[candidate.station_id].status,
        ) if candidate.station_id in coverage else candidate
        for candidate in candidates
    ]
    return url, enriched


def build_discharge_query(spec: SiteSpec, station_id: str) -> tuple[str, dict[str, str]]:
    if not station_id.isdigit():
        raise WatershedDataError("USGS station ID must contain digits only")
    parameters = {
        "format": "json", "sites": station_id, "parameterCd": "00060",
        "startDT": spec.study_start, "endDT": spec.study_end, "siteStatus": "all",
    }
    return USGS_INSTANTANEOUS_VALUES_SERVICE, parameters


def summarize_discharge_json(raw: bytes, station_id: str) -> dict[str, object]:
    try:
        document = json.loads(raw)
        series = document["value"]["timeSeries"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise WatershedDataError("USGS discharge response is not valid WaterML JSON") from exc
    matching = []
    for item in series:
        source = item.get("sourceInfo") or {}
        codes = source.get("siteCode") or []
        site_codes = {str(code.get("value")) for code in codes if isinstance(code, dict)}
        variable = item.get("variable") or {}
        variable_codes = variable.get("variableCode") or []
        parameter_codes = {str(code.get("value")) for code in variable_codes if isinstance(code, dict)}
        if station_id in site_codes and "00060" in parameter_codes:
            matching.append(item)
    if not matching:
        raise WatershedDataError(f"USGS response has no discharge series for station {station_id}")
    observations = []
    unit_codes = set()
    no_data_values = set()
    for item in matching:
        variable = item.get("variable") or {}
        unit = variable.get("unit") or {}
        if unit.get("unitCode"):
            unit_codes.add(str(unit["unitCode"]))
        if variable.get("noDataValue") is not None:
            no_data_values.add(str(variable["noDataValue"]))
        for block in item.get("values") or []:
            observations.extend(block.get("value") or [])
    timestamps = sorted(
        str(item["dateTime"]) for item in observations if item.get("dateTime")
    )
    qualifiers = sorted({
        str(qualifier)
        for item in observations for qualifier in (item.get("qualifiers") or [])
    })
    if not timestamps:
        raise WatershedDataError(f"USGS response has no observations for station {station_id}")
    return {
        "station_id": station_id, "variable": "discharge", "parameter_code": "00060",
        "native_units": sorted(unit_codes), "observation_count": len(observations),
        "temporal_coverage": {"start": timestamps[0], "end": timestamps[-1]},
        "qualifiers": qualifiers, "no_data_values": sorted(no_data_values),
        "timezone_semantics": "offset_preserved_in_native_timestamps",
    }


def acquire_observed_discharge(
    spec: SiteSpec,
    station_id: str,
    *,
    cache: str | Path,
    catalog: str | Path,
    opener: Callable[..., object] = urllib.request.urlopen,
    refresh: bool = False,
) -> dict[str, object]:
    endpoint, parameters = build_discharge_query(spec, station_id)
    request_key = canonical_request_key("usgs", endpoint, parameters, "nwis-iv-waterml-1.1")
    catalog_store = AssetCatalog(catalog)
    if not refresh and (cached := catalog_store.cached_request(request_key, cache)) is not None:
        return cached
    url = endpoint + "?" + urllib.parse.urlencode(parameters)
    raw, _, acquisition_attempts = download_bytes(
        url, opener=opener, timeout=120.0, label="USGS discharge acquisition"
    )
    summary = summarize_discharge_json(raw, station_id)
    stored = ObjectStore(cache).put(io.BytesIO(raw))
    return catalog_store.register({
        "provider": "usgs", "product": "observed-discharge",
        "product_version": "nwis-iv-waterml-1.1", "station_id": station_id,
        "request_parameters": parameters, "request_key": request_key,
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "source_url": url,
        "processing_status": "native", "acquisition_attempts": acquisition_attempts, **summary,
    })

from __future__ import annotations

import csv
import io
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .schemas import SiteSpec, WatershedDataError

USGS_SITE_SERVICE = "https://waterservices.usgs.gov/nwis/site/"


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
    latitude_delta = radius_km / 111.32
    longitude_delta = radius_km / (111.32 * max(0.1, math.cos(math.radians(spec.latitude))))
    return ",".join(str(value) for value in (
        spec.longitude - longitude_delta, spec.latitude - latitude_delta,
        spec.longitude + longitude_delta, spec.latitude + latitude_delta,
    ))


def build_site_query(spec: SiteSpec, radius_km: float) -> str:
    parameters = {
        "format": "rdb", "bBox": _bounding_box(spec, radius_km),
        "parameterCd": "00060", "siteStatus": "all", "siteOutput": "expanded",
    }
    return USGS_SITE_SERVICE + "?" + urllib.parse.urlencode(parameters)


def parse_site_rdb(text: str, spec: SiteSpec) -> list[GaugeCandidate]:
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 2:
        return []
    reader = csv.DictReader(io.StringIO("\n".join([lines[0], *lines[2:]])), delimiter="\t")
    candidates = []
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
        candidates.append(GaugeCandidate(
            provider="usgs", station_id=row["site_no"], name=row.get("station_nm") or "",
            longitude=longitude, latitude=latitude,
            distance_km=_haversine_km(spec.longitude, spec.latitude, longitude, latitude),
            drainage_area_km2=area, record_start=row.get("begin_date") or None,
            record_end=row.get("end_date") or None,
            status=(row.get("site_status") or row.get("site_tp_cd") or "unknown").lower(),
        ))
    return sorted(candidates, key=lambda item: (item.distance_km, item.station_id))


def discover_gauges(
    spec: SiteSpec,
    *,
    radius_km: float = 50.0,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> tuple[str, list[GaugeCandidate]]:
    url = build_site_query(spec, radius_km)
    try:
        with opener(url, timeout=60.0) as response:
            text = response.read().decode("utf-8")
    except OSError as exc:
        raise WatershedDataError(f"USGS gauge discovery failed: {exc}") from exc
    return url, parse_site_rdb(text, spec)

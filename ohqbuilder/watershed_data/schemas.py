from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class WatershedDataError(ValueError):
    """Raised when a watershed-data document violates its contract."""


def _utc_timestamp(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise WatershedDataError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WatershedDataError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WatershedDataError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    """Serialize JSON data deterministically for identity calculations."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WatershedDataError(f"value is not canonical JSON data: {exc}") from exc


def canonical_request_key(
    provider: str,
    endpoint: str,
    parameters: dict[str, Any],
    product_version: str,
    *,
    method: str = "GET",
) -> str:
    """Return the logical request identity, independent of response bytes."""

    document = {
        "endpoint": endpoint,
        "method": method.upper(),
        "parameters": parameters,
        "product_version": product_version,
        "provider": provider,
    }
    return hashlib.sha256(canonical_json(document)).hexdigest()


@dataclass(frozen=True)
class SiteSpec:
    site_id: str
    name: str
    longitude: float
    latitude: float
    study_start: str
    study_end: str
    target_timestep: str
    sources: dict[str, Any]
    schema_version: str = "1.0"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteSpec":
        if not isinstance(data, dict):
            raise WatershedDataError("SiteSpec must be an object")
        geometry = data.get("geometry") or {}
        outlet = geometry.get("outlet") or {}
        period = data.get("study_period") or {}
        site_id = str(data.get("site_id") or "").strip()
        if not site_id:
            raise WatershedDataError("site_id is required")
        try:
            longitude = float(outlet["longitude"])
            latitude = float(outlet["latitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WatershedDataError("geometry.outlet longitude and latitude are required") from exc
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise WatershedDataError("outlet longitude or latitude is outside its valid range")
        start = _utc_timestamp(period.get("start"), "study_period.start")
        end = _utc_timestamp(period.get("end"), "study_period.end")
        if start >= end:
            raise WatershedDataError("study_period.start must precede study_period.end")
        sources = data.get("sources") or {}
        if not isinstance(sources, dict):
            raise WatershedDataError("sources must be an object")
        return cls(
            site_id=site_id,
            name=str(data.get("name") or site_id),
            longitude=longitude,
            latitude=latitude,
            study_start=start,
            study_end=end,
            target_timestep=str(data.get("target_timestep") or "1h"),
            sources=sources,
            schema_version=str(data.get("schema_version") or "1.0"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "SiteSpec":
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WatershedDataError(f"could not read SiteSpec {path}: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": "SiteSpec",
            "schema_version": self.schema_version,
            "site_id": self.site_id,
            "name": self.name,
            "geometry": {
                "outlet": {"longitude": self.longitude, "latitude": self.latitude},
                "supplied_basin": None,
            },
            "study_period": {"start": self.study_start, "end": self.study_end},
            "target_timestep": self.target_timestep,
            "sources": self.sources,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

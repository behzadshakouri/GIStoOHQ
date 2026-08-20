from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .catalog import AssetCatalog, ObjectStore
from .network import download_bytes
from .schemas import WatershedDataError, canonical_request_key


def _time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise WatershedDataError(f"forecast {field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WatershedDataError(f"forecast {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_forecast_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    required = {"issue_time", "valid_time", "lead_time_hours", "member", "variable",
                "location_or_grid_id", "value", "units"}
    if not records:
        raise WatershedDataError("forecast archive contains no records")
    issues, valids, variables, members, locations = [], [], set(), set(), set()
    for index, record in enumerate(records):
        missing = sorted(required - record.keys())
        if missing:
            raise WatershedDataError(f"forecast record {index} is missing: {', '.join(missing)}")
        issue, valid = _time(record["issue_time"], "issue_time"), _time(record["valid_time"], "valid_time")
        if issue > valid:
            raise WatershedDataError(f"forecast record {index} has issue_time after valid_time")
        expected = (valid - issue).total_seconds() / 3600
        if abs(float(record["lead_time_hours"]) - expected) > 1e-6:
            raise WatershedDataError(f"forecast record {index} has inconsistent lead_time_hours")
        issues.append(issue)
        valids.append(valid)
        variables.add(str(record["variable"]))
        members.add(str(record["member"]))
        locations.add(str(record["location_or_grid_id"]))
    return {
        "record_count": len(records), "variables": sorted(variables), "members": sorted(members),
        "location_or_grid_ids": sorted(locations),
        "issue_time_coverage": {"start": min(issues).isoformat(), "end": max(issues).isoformat()},
        "valid_time_coverage": {"start": min(valids).isoformat(), "end": max(valids).isoformat()},
        "availability_rule": "issue_time_must_not_exceed_prediction_time",
    }


def acquire_forecast_archive(
    *, url: str, provider: str, product: str, cache: str | Path, catalog: str | Path,
    opener: Callable[..., object] = urllib.request.urlopen,
    refresh: bool = False,
) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise WatershedDataError("forecast archive URL must use HTTPS")
    parameters = {"url": url}
    request_key = canonical_request_key(provider, url, parameters, "forecast-records-v1")
    catalog_store = AssetCatalog(catalog)
    if not refresh and (cached := catalog_store.cached_request(request_key, cache)) is not None:
        return cached
    raw, _ = download_bytes(
        url, opener=opener, timeout=120.0, label="forecast archive acquisition"
    )
    try:
        records = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WatershedDataError(f"could not acquire forecast archive: {exc}") from exc
    if not isinstance(records, list):
        raise WatershedDataError("forecast archive must be a JSON array")
    summary = validate_forecast_records(records)
    stored = ObjectStore(cache).put(io.BytesIO(raw))
    return catalog_store.register({
        "provider": provider, "product": product, "product_version": "forecast-records-v1",
        "request_key": request_key,
        "request_parameters": parameters, "content_digest": stored.content_digest,
        "size": stored.size, "media_type": "application/json", "source_url": url,
        "processing_status": "native", "temporal_dimensions": [
            "issue_time", "valid_time", "lead_time_hours", "member",
        ], **summary,
    })


def materialize_available_forecasts(
    *, asset_id: str, prediction_time: str, catalog: str | Path, object_store: str | Path,
) -> dict[str, Any]:
    cutoff = _time(prediction_time, "prediction_time")
    catalog_store = AssetCatalog(catalog)
    source = next((item for item in catalog_store.read()["assets"] if item["asset_id"] == asset_id), None)
    if source is None:
        raise WatershedDataError(f"forecast asset not found: {asset_id}")
    with ObjectStore(object_store).open(source["content_digest"]) as stream:
        records = json.load(stream)
    validate_forecast_records(records)
    available = [record for record in records if _time(record["issue_time"], "issue_time") <= cutoff]
    buffer = io.StringIO(newline="")
    fields = ["issue_time", "valid_time", "lead_time_hours", "member", "variable",
              "location_or_grid_id", "value", "units"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(available, key=lambda row: (row["issue_time"], row["valid_time"], row["member"])))
    stored = ObjectStore(object_store).put(io.BytesIO(buffer.getvalue().encode()))
    identity = {"parent": asset_id, "prediction_time": cutoff.isoformat()}
    return catalog_store.register({
        "provider": source["provider"], "product": "available-forecast-view",
        "product_version": "1.0", "request_key": hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest(), "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "text/csv", "processing_status": "derived",
        "parent_asset_ids": [asset_id], "prediction_time": cutoff.isoformat(),
        "record_count": len(available), "leakage_rule": "issue_time <= prediction_time",
    })

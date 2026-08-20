from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .schemas import ProvenanceActivity, QCResult, WatershedDataError


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WatershedDataError(f"invalid observation timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise WatershedDataError(f"observation timestamp has no timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _usgs_rows(document: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows, units = [], {}
    for series in document.get("value", {}).get("timeSeries", []):
        variable = series.get("variable") or {}
        codes = variable.get("variableCode") or []
        code = str(codes[0].get("value")) if codes else "unknown"
        units[code] = str((variable.get("unit") or {}).get("unitCode") or "unknown")
        no_data = variable.get("noDataValue")
        for block in series.get("values") or []:
            for item in block.get("value") or []:
                raw = item.get("value")
                value = None if raw is None or str(raw) == str(no_data) else float(raw)
                rows.append({
                    "timestamp": _utc(str(item["dateTime"])), "variable": code,
                    "value": value, "qualifiers": ";".join(item.get("qualifiers") or []),
                })
    return rows, units


def _power_rows(document: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows = []
    parameters = document.get("properties", {}).get("parameter", {})
    metadata = document.get("parameters", {})
    units = {code: str((metadata.get(code) or {}).get("units") or "unknown") for code in parameters}
    for code, values in parameters.items():
        for timestamp, raw in values.items():
            try:
                timestamp_format = "%Y%m%d%H" if len(timestamp) == 10 else "%Y%m%d"
                parsed = datetime.strptime(timestamp, timestamp_format).replace(tzinfo=timezone.utc)
            except ValueError as exc:
                raise WatershedDataError(f"invalid NASA POWER timestamp: {timestamp}") from exc
            rows.append({
                "timestamp": parsed, "variable": code,
                "value": None if raw in (-999, -999.0, None) else float(raw), "qualifiers": "",
            })
    return rows, units


def _native_rows(raw: bytes, provider: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WatershedDataError("native temporal asset is not JSON") from exc
    if provider == "usgs":
        return _usgs_rows(document)
    if provider == "nasa-power":
        return _power_rows(document)
    raise WatershedDataError(f"temporal harmonization does not support provider {provider!r}")


def temporal_qc(
    rows: list[dict[str, Any]], asset_id: str, temporal_resolution: str | None = None,
    units: dict[str, str] | None = None,
) -> list[QCResult]:
    keys = [(row["timestamp"], row["variable"]) for row in rows]
    duplicates = len(keys) - len(set(keys))
    missing = sum(row["value"] is None for row in rows)
    ordered = keys == sorted(keys)
    ranges = {
        "00060": (0.0, None), "PRECTOTCORR": (0.0, None), "RH2M": (0.0, 100.0),
        "WS2M": (0.0, None), "ALLSKY_SFC_SW_DWN": (0.0, None),
        "EVPTRNS": (0.0, None), "T2M": (-100.0, 70.0),
    }
    violations = []
    for row in rows:
        bounds = ranges.get(row["variable"])
        value = row["value"]
        if bounds is None or value is None:
            continue
        minimum, maximum = bounds
        if value < minimum or (maximum is not None and value > maximum):
            violations.append({
                "timestamp": row["timestamp"].isoformat(), "variable": row["variable"],
                "value": value, "minimum": minimum, "maximum": maximum,
            })
    expected_delta = {"hourly": timedelta(hours=1), "daily": timedelta(days=1)}.get(
        temporal_resolution or ""
    )
    missing_intervals = 0
    gap_examples = []
    if expected_delta is not None:
        by_variable: dict[str, list[datetime]] = {}
        for row in rows:
            by_variable.setdefault(row["variable"], []).append(row["timestamp"])
        for variable, timestamps in by_variable.items():
            unique = sorted(set(timestamps))
            for previous, current in zip(unique, unique[1:]):
                gap = current - previous
                if gap > expected_delta:
                    count = max(0, int(gap / expected_delta) - 1)
                    missing_intervals += count
                    if len(gap_examples) < 100:
                        gap_examples.append({
                            "variable": variable, "after": previous.isoformat(),
                            "before": current.isoformat(), "missing_intervals": count,
                        })
    expected_units = {
        "00060": {"ft3/s", "m3/s"}, "PRECTOTCORR": {"mm/hour"},
        "T2M": {"C"}, "RH2M": {"%"}, "WS2M": {"m/s"},
        "ALLSKY_SFC_SW_DWN": {"kW-hr/m^2"}, "EVPTRNS": {"mm/day"},
    }
    unit_mismatches = []
    observed_variables = sorted({row["variable"] for row in rows})
    for variable in observed_variables:
        allowed = expected_units.get(variable)
        if allowed is None:
            continue
        actual = (units or {}).get(variable, "unknown")
        if actual not in allowed:
            unit_mismatches.append({
                "variable": variable, "actual_unit": actual, "allowed_units": sorted(allowed),
            })
    return [
        QCResult("temporal.duplicate_timestamps", "error", duplicates == 0,
                 f"{duplicates} duplicate timestamp-variable records", (asset_id,),
                 {"duplicate_count": duplicates}),
        QCResult("temporal.missing_values", "warning", missing == 0,
                 f"{missing} missing values", (asset_id,), {"missing_count": missing}),
        QCResult("temporal.chronology", "warning", ordered,
                 "records are chronologically ordered" if ordered else "native records are unordered",
                 (asset_id,)),
        QCResult(
            "temporal.physical_range", "error", not violations,
            f"{len(violations)} values outside declared physical ranges", (asset_id,),
            {"violation_count": len(violations), "violations": violations[:100]},
        ),
        QCResult(
            "temporal.unit_compatibility", "error", not unit_mismatches,
            f"{len(unit_mismatches)} known variables have incompatible native units",
            (asset_id,), {
                "mismatch_count": len(unit_mismatches), "mismatches": unit_mismatches,
                "unknown_variables_not_evaluated": sorted(
                    set(observed_variables) - set(expected_units)
                ),
            },
        ),
        QCResult(
            "temporal.expected_intervals", "warning",
            expected_delta is None or missing_intervals == 0,
            "native temporal resolution is not fixed" if expected_delta is None else
            f"{missing_intervals} expected internal intervals are missing",
            (asset_id,), {
                "evaluated": expected_delta is not None,
                "temporal_resolution": temporal_resolution,
                "missing_interval_count": missing_intervals,
                "gaps": gap_examples,
            },
        ),
    ]


def harmonize_asset(
    *, asset_id: str, catalog: str | Path, object_store: str | Path,
    qc_output: str | Path, provenance_output: str | Path,
) -> dict[str, Any]:
    catalog_store = AssetCatalog(catalog)
    catalog_data = catalog_store.read()
    source = next((asset for asset in catalog_data["assets"] if asset["asset_id"] == asset_id), None)
    if source is None:
        raise WatershedDataError(f"asset not found in catalog: {asset_id}")
    with ObjectStore(object_store).open(source["content_digest"]) as stream:
        rows, units = _native_rows(stream.read(), source["provider"])
    if not rows:
        raise WatershedDataError("native temporal asset contains no observations")
    qc = temporal_qc(rows, asset_id, source.get("temporal_resolution"), units)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("timestamp_utc", "variable", "value", "native_unit", "provider_qualifiers"))
    for row in sorted(rows, key=lambda item: (item["timestamp"], item["variable"])):
        timestamp = row["timestamp"].isoformat().replace("+00:00", "Z")
        writer.writerow((timestamp, row["variable"], "" if row["value"] is None else row["value"],
                         units.get(row["variable"], "unknown"), row["qualifiers"]))
    stored = ObjectStore(object_store).put(io.BytesIO(buffer.getvalue().encode("utf-8")))
    started = completed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    transformation = {"target_timezone": "UTC", "ordering": "timestamp_then_variable",
                      "missing_values": "preserved", "unit_conversion": "none"}
    output = catalog_store.register({
        "provider": source["provider"], "product": "harmonized-temporal-observations",
        "product_version": "1.0", "request_key": hashlib.sha256(
            json.dumps({"parent": asset_id, **transformation}, sort_keys=True).encode()
        ).hexdigest(), "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "text/csv", "processing_status": "derived",
        "parent_asset_ids": [asset_id], "native_units": units,
        "temporal_resolution": source.get("temporal_resolution", "native_support"),
        "transformation_name": "native-to-utc-table", "transformation_version": "1.1",
        "transformation_parameters": transformation,
    })
    activity = ProvenanceActivity(
        activity_id="sha256:" + hashlib.sha256(f"{asset_id}:{output['asset_id']}".encode()).hexdigest(),
        transformation_name="native-to-utc-table", transformation_version="1.1",
        parent_asset_ids=(asset_id,), output_asset_ids=(output["asset_id"],),
        parameters=transformation, software_version="GIStoOHQ-0.1.0",
        started_at=started, completed_at=completed,
    )
    _atomic_json(Path(qc_output), {"schema_name": "QCReport", "schema_version": "1.0",
                                  "results": [item.to_dict() for item in qc]})
    _atomic_json(Path(provenance_output), activity.to_dict())
    return output

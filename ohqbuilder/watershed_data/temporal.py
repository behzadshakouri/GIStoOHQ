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
    expected_start: str | None = None, expected_end: str | None = None,
) -> list[QCResult]:
    rows_by_variable: dict[str, list[dict[str, Any]]] = {}
    duplicates = 0
    duplicate_examples = []
    seen_keys = set()
    for row in rows:
        timestamp, variable = row["timestamp"], row["variable"]
        rows_by_variable.setdefault(variable, []).append(row)
        key = (timestamp, variable)
        if key in seen_keys:
            duplicates += 1
            if len(duplicate_examples) < 100:
                duplicate_examples.append({
                    "timestamp": timestamp.isoformat(), "variable": variable,
                })
        seen_keys.add(key)
    missing = sum(row["value"] is None for row in rows)
    completeness_by_variable = {}
    missing_examples = []
    for variable, variable_rows in sorted(rows_by_variable.items()):
        variable_missing = sum(row["value"] is None for row in variable_rows)
        completeness_by_variable[variable] = {
            "record_count": len(variable_rows),
            "valid_count": len(variable_rows) - variable_missing,
            "missing_count": variable_missing,
            "missing_fraction": variable_missing / len(variable_rows),
        }
    for row in rows:
        if row["value"] is None and len(missing_examples) < 100:
            missing_examples.append({
                "timestamp": row["timestamp"].isoformat(), "variable": row["variable"],
            })
    chronology_inversions = 0
    chronology_examples = []
    for variable, variable_rows in sorted(rows_by_variable.items()):
        timestamps = [row["timestamp"] for row in variable_rows]
        for previous, current in zip(timestamps, timestamps[1:]):
            if current < previous:
                chronology_inversions += 1
                if len(chronology_examples) < 100:
                    chronology_examples.append({
                        "variable": variable,
                        "previous_timestamp": previous.isoformat(),
                        "current_timestamp": current.isoformat(),
                    })
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
    alignment_origin = datetime(1970, 1, 1, tzinfo=timezone.utc)
    misaligned = []
    misaligned_count = 0
    if expected_delta is not None:
        interval_seconds = expected_delta.total_seconds()
        for row in rows:
            offset_seconds = (row["timestamp"] - alignment_origin).total_seconds()
            if offset_seconds % interval_seconds:
                misaligned_count += 1
                if len(misaligned) < 100:
                    misaligned.append({
                        "timestamp": row["timestamp"].isoformat(),
                        "variable": row["variable"],
                    })
    requested_start = _utc(expected_start) if expected_start else None
    requested_end = _utc(expected_end) if expected_end else None
    coverage_tolerance = expected_delta or timedelta(0)
    coverage_gaps = []
    coverage_by_variable = {}
    sorted_timestamps_by_variable = ({
        variable: sorted({row["timestamp"] for row in variable_rows})
        for variable, variable_rows in rows_by_variable.items()
    } if expected_delta is not None else {})
    for variable, variable_rows in sorted(rows_by_variable.items()):
        valid_timestamps = [
            row["timestamp"] for row in variable_rows if row["value"] is not None
        ]
        observed_start = min(valid_timestamps, default=None)
        observed_end = max(valid_timestamps, default=None)
        coverage_by_variable[variable] = {
            "observed_start": observed_start.isoformat() if observed_start else None,
            "observed_end": observed_end.isoformat() if observed_end else None,
        }
        if requested_start is not None and (
            observed_start is None or observed_start > requested_start
        ):
            coverage_gaps.append({
                "variable": variable, "boundary": "start",
                "requested": requested_start.isoformat(),
                "observed": observed_start.isoformat() if observed_start else None,
                "gap_seconds": (
                    (observed_start - requested_start).total_seconds()
                    if observed_start else None
                ),
            })
        if requested_end is not None and (
            observed_end is None or observed_end + coverage_tolerance < requested_end
        ):
            coverage_gaps.append({
                "variable": variable, "boundary": "end",
                "requested": requested_end.isoformat(),
                "observed": observed_end.isoformat() if observed_end else None,
                "gap_seconds": (
                    (requested_end - observed_end).total_seconds()
                    if observed_end else None
                ),
            })
    missing_intervals = 0
    missing_intervals_by_variable = {}
    gap_examples = []
    if expected_delta is not None:
        for variable in sorted(rows_by_variable):
            variable_missing_intervals = 0
            timestamps = sorted_timestamps_by_variable[variable]
            for previous, current in zip(timestamps, timestamps[1:]):
                gap = current - previous
                if gap > expected_delta:
                    count = max(0, int(gap / expected_delta) - 1)
                    missing_intervals += count
                    variable_missing_intervals += count
                    if len(gap_examples) < 100:
                        gap_examples.append({
                            "variable": variable, "after": previous.isoformat(),
                            "before": current.isoformat(), "missing_intervals": count,
                        })
            missing_intervals_by_variable[variable] = variable_missing_intervals
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
    qualifier_counts: dict[str, int] = {}
    provisional_records = 0
    for row in rows:
        qualifiers = {value for value in str(row.get("qualifiers") or "").split(";") if value}
        for qualifier in qualifiers:
            qualifier_counts[qualifier] = qualifier_counts.get(qualifier, 0) + 1
        if "P" in qualifiers:
            provisional_records += 1
    return [
        QCResult("temporal.duplicate_timestamps", "error", duplicates == 0,
                 f"{duplicates} duplicate timestamp-variable records", (asset_id,),
                 {"duplicate_count": duplicates, "examples": duplicate_examples}),
        QCResult("temporal.missing_values", "warning", missing == 0,
                 f"{missing} missing values", (asset_id,), {
                     "missing_count": missing,
                     "completeness_by_variable": completeness_by_variable,
                     "examples": missing_examples,
                 }),
        QCResult(
            "temporal.chronology", "warning", chronology_inversions == 0,
            "each variable is chronologically ordered" if chronology_inversions == 0 else
            f"{chronology_inversions} within-variable chronology inversions",
            (asset_id,), {
                "inversion_count": chronology_inversions,
                "examples": chronology_examples,
            },
        ),
        QCResult(
            "temporal.physical_range", "error", not violations,
            f"{len(violations)} values outside declared physical ranges", (asset_id,),
            {"violation_count": len(violations), "violations": violations[:100]},
        ),
        QCResult(
            "temporal.provider_qualifiers", "warning", provisional_records == 0,
            f"{provisional_records} records carry the USGS provisional qualifier",
            (asset_id,), {
                "provisional_record_count": provisional_records,
                "qualifier_counts": dict(sorted(qualifier_counts.items())),
                "interpretation": {"A": "approved", "P": "provisional"},
            },
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
                "missing_intervals_by_variable": missing_intervals_by_variable,
                "gaps": gap_examples,
            },
        ),
        QCResult(
            "temporal.timestep_alignment", "warning",
            expected_delta is None or misaligned_count == 0,
            "native temporal resolution is not fixed" if expected_delta is None else
            f"{misaligned_count} records are not aligned to the {temporal_resolution} UTC grid",
            (asset_id,), {
                "evaluated": expected_delta is not None,
                "temporal_resolution": temporal_resolution,
                "misaligned_record_count": misaligned_count,
                "examples": misaligned,
            },
        ),
        QCResult(
            "temporal.study_period_coverage", "warning", not coverage_gaps,
            "study-period bounds were not supplied" if not (requested_start or requested_end)
            else (f"{len(coverage_gaps)} variable boundaries do not cover the study period"
                  if coverage_gaps else "all variables cover the requested study period"),
            (asset_id,), {
                "evaluated": bool(requested_start or requested_end),
                "requested_start": requested_start.isoformat() if requested_start else None,
                "requested_end": requested_end.isoformat() if requested_end else None,
                "coverage_by_variable": coverage_by_variable,
                "end_tolerance_seconds": coverage_tolerance.total_seconds(),
                "uncovered_boundaries": coverage_gaps,
            },
        ),
    ]


def harmonize_asset(
    *, asset_id: str, catalog: str | Path, object_store: str | Path,
    qc_output: str | Path, provenance_output: str | Path,
    expected_start: str | None = None, expected_end: str | None = None,
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
    qc = temporal_qc(
        rows, asset_id, source.get("temporal_resolution"), units,
        expected_start, expected_end,
    )
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

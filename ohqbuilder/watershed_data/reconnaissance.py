from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import SiteSpec, WatershedDataError
from .usgs import GaugeCandidate, discover_gauges


@dataclass(frozen=True)
class CandidateAssessment:
    candidate: GaugeCandidate
    score: float
    acceptable: bool
    constraints: dict[str, bool | None]
    rejection_reasons: tuple[str, ...]
    metrics: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.candidate.to_dict(), "score": self.score, "acceptable": self.acceptable,
            "constraints": self.constraints, "rejection_reasons": list(self.rejection_reasons),
            "metrics": self.metrics,
        }


def _discharge_policy(spec: SiteSpec) -> dict[str, Any]:
    source = spec.sources.get("discharge") or {}
    if isinstance(source, str):
        source = {"selection": source}
    if not isinstance(source, dict):
        raise WatershedDataError("sources.discharge must be an object")
    constraints = source.get("constraints") or {}
    if not isinstance(constraints, dict):
        raise WatershedDataError("sources.discharge.constraints must be an object")
    return constraints


def assess_candidate(candidate: GaugeCandidate, spec: SiteSpec) -> CandidateAssessment:
    policy = _discharge_policy(spec)
    maximum_distance = float(policy.get("maximum_distance_km", 50.0))
    study_start, study_end = spec.study_start[:10], spec.study_end[:10]
    overlap = bool(
        candidate.record_start and candidate.record_end
        and candidate.record_start <= study_end and candidate.record_end >= study_start
    )
    constraints: dict[str, bool | None] = {
        "maximum_distance": candidate.distance_km <= maximum_distance,
        "study_period_overlap": overlap,
        "topological_compatibility": None,
        "drainage_area_compatibility": None,
    }
    compatible_stations = policy.get("topologically_compatible_station_ids")
    if compatible_stations is not None:
        if not isinstance(compatible_stations, list) or any(
            not str(station).isdigit() for station in compatible_stations
        ):
            raise WatershedDataError(
                "topologically_compatible_station_ids must be an array of numeric station IDs"
            )
        constraints["topological_compatibility"] = candidate.station_id in {
            str(station) for station in compatible_stations
        }
    expected_area = policy.get("expected_drainage_area_km2")
    maximum_area_error = policy.get("maximum_drainage_area_error_fraction")
    if expected_area is not None:
        expected_area = float(expected_area)
        if expected_area <= 0:
            raise WatershedDataError("expected_drainage_area_km2 must be positive")
    if maximum_area_error is not None:
        maximum_area_error = float(maximum_area_error)
        if not 0 <= maximum_area_error <= 1 or expected_area is None:
            raise WatershedDataError(
                "maximum_drainage_area_error_fraction requires expected area and range [0,1]"
            )
    area_error = None
    if expected_area is not None and candidate.drainage_area_km2 is not None:
        area_error = abs(candidate.drainage_area_km2 - expected_area) / expected_area
    if maximum_area_error is not None:
        constraints["drainage_area_compatibility"] = (
            area_error is not None and area_error <= maximum_area_error
        )
    reasons = []
    if not constraints["maximum_distance"]:
        reasons.append(f"distance exceeds {maximum_distance:g} km")
    if policy.get("require_study_period_overlap", True) and not overlap:
        reasons.append("record does not overlap the study period")
    if policy.get("require_topological_compatibility", False) and not constraints[
        "topological_compatibility"
    ]:
        reasons.append(
            "topological compatibility is not established" if compatible_stations is None else
            "station is not in the declared topologically compatible set"
        )
    if constraints["drainage_area_compatibility"] is False:
        reasons.append(
            "gauge drainage area is unavailable" if area_error is None else
            f"drainage-area error {area_error:.3f} exceeds {maximum_area_error:.3f}"
        )
    allowed_statuses = [str(value).lower() for value in policy.get("allowed_statuses", [])]
    if allowed_statuses:
        constraints["allowed_status"] = candidate.status in allowed_statuses
        if not constraints["allowed_status"]:
            reasons.append(f"station status {candidate.status!r} is not allowed")
    score = max(0.0, 100.0 - min(candidate.distance_km, 100.0))
    if overlap:
        score += 25.0
    if area_error is not None:
        score += max(0.0, 20.0 * (1.0 - min(area_error, 1.0)))
    return CandidateAssessment(
        candidate, round(score, 6), not reasons, constraints, tuple(reasons),
        {"drainage_area_error_fraction": area_error},
    )


def run_reconnaissance(
    site_spec: str | Path, output: str | Path, *, radius_km: float = 50.0,
    discover=discover_gauges,
) -> dict[str, Any]:
    spec = SiteSpec.from_file(site_spec)
    query_url, candidates = discover(spec, radius_km=radius_km)
    assessed = sorted(
        (assess_candidate(candidate, spec) for candidate in candidates),
        key=lambda item: (-item.score, item.candidate.station_id),
    )
    acceptable = [item for item in assessed if item.acceptable]
    if not acceptable:
        decision, selected = "no_acceptable_candidate", None
    elif len(acceptable) == 1 or acceptable[0].score > acceptable[1].score:
        decision, selected = "selected", acceptable[0].candidate.station_id
    else:
        decision, selected = "ambiguous_candidates", None
    report = {
        "schema_name": "ReconnaissanceReport", "schema_version": "1.0",
        "site_id": spec.site_id, "site_spec_digest": spec.digest,
        "provider": "usgs", "query_url": query_url, "decision": decision,
        "selected_station_id": selected, "candidates": [item.to_dict() for item in assessed],
    }
    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    rows = ["# Gauge reconnaissance", "", f"Decision: **{decision}**", "",
            "| Station | Name | Distance (km) | Score | Acceptable | Reasons |",
            "| --- | --- | ---: | ---: | --- | --- |"]
    for item in assessed:
        reasons = "; ".join(item.rejection_reasons) or "—"
        rows.append(
            f"| {item.candidate.station_id} | {item.candidate.name} | "
            f"{item.candidate.distance_km:.3f} | {item.score:.3f} | "
            f"{'yes' if item.acceptable else 'no'} | {reasons} |"
        )
    (destination / "report.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return report


def selected_station_from_report(path: str | Path) -> str:
    """Return an unambiguous selected station from an existing report."""
    candidate = Path(path).expanduser().resolve()
    report_path = candidate / "report.json" if candidate.is_dir() else candidate
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatershedDataError(f"could not read reconnaissance report: {exc}") from exc
    if report.get("schema_name") != "ReconnaissanceReport":
        raise WatershedDataError("selected gauge requires a ReconnaissanceReport")
    if report.get("decision") != "selected" or not report.get("selected_station_id"):
        raise WatershedDataError(
            f"reconnaissance has no unambiguous selection: {report.get('decision', 'unknown')}"
        )
    station_id = str(report["selected_station_id"])
    if not station_id.isdigit():
        raise WatershedDataError("selected USGS station ID must contain digits only")
    return station_id

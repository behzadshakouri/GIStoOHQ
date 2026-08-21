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

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.candidate.to_dict(), "score": self.score, "acceptable": self.acceptable,
            "constraints": self.constraints, "rejection_reasons": list(self.rejection_reasons),
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
    }
    reasons = []
    if not constraints["maximum_distance"]:
        reasons.append(f"distance exceeds {maximum_distance:g} km")
    if policy.get("require_study_period_overlap", True) and not overlap:
        reasons.append("record does not overlap the study period")
    if policy.get("require_topological_compatibility", False):
        reasons.append("topological compatibility is not established")
    allowed_statuses = [str(value).lower() for value in policy.get("allowed_statuses", [])]
    if allowed_statuses:
        constraints["allowed_status"] = candidate.status in allowed_statuses
        if not constraints["allowed_status"]:
            reasons.append(f"station status {candidate.status!r} is not allowed")
    score = max(0.0, 100.0 - min(candidate.distance_km, 100.0))
    if overlap:
        score += 25.0
    return CandidateAssessment(candidate, round(score, 6), not reasons, constraints, tuple(reasons))


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

"""Aggregate GIStoOHQ's own comparison reports into one delineation-confidence summary.

GIStoOHQ already computes outlet-snap distance (dem_workflow.py), boundary
agreement against WBD/documented/NHDPlus references (watershed_comparison.py),
and reach-network agreement against NHD flowlines (reach_comparison.py). Each
lives in its own JSON file and none of them is surfaced as a single, prominent
"how much should I trust this delineation" signal in the watershed report -
a reviewer has to know to look for and cross-reference several files by hand.

This module reads whichever of those reports already exist for a run and
aggregates them into one summary with a plain-language confidence rating and
explicit caveats. It computes nothing new from geometry itself, so it is only
ever as good as the reports it is aggregating - if a comparison step was
skipped (no WBD/documented/NHDPlus reference was available), the corresponding
part of this summary is 'unknown', not silently omitted.

Motivated by comparing GIStoOHQ with BasinKit (github.com/Praddy-GByte/basinkit):
BasinKit's own published validation reports a 181% median area error for
catchments under 100 km^2 using its default global delineation backend - the
same size class GIStoOHQ's own typical sites (e.g. Sligo Creek, ~24-39 km^2)
fall into. Small urban catchments are the hardest case for automated
delineation in general, so a GIStoOHQ delineation should carry its own
confidence signal rather than imply a false sense of precision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BOUNDARY_COMPARISON_REPORTS = (
    ("wbd", "watershed_wbd_comparison.json"),
    ("documented", "watershed_documented_comparison.json"),
    ("nhdplus", "watershed_nhdplus_comparison.json"),
)
INFORMATIONAL_REFERENCE_ROLES = {"acquisition_estimate"}
REACH_COMPARISON_REPORT = "reaches_nhd_comparison.json"
DEM_WORKFLOW_SUMMARY = "intermediate/dem_workflow_summary.json"

# Matches the GREEN/YELLOW/RED thresholds already documented (but previously
# unpopulated with an actual value) in the watershed report's outlet-snap
# legend.
SNAP_GREEN_M = 20.0
SNAP_YELLOW_M = 75.0


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _boundary_confidence(best_match: dict[str, Any]) -> tuple[str, list[str]]:
    """Classify one boundary comparison's agreement; list the reasons why."""

    caveats: list[str] = []
    iou = best_match.get("iou")
    area_ratio = best_match.get("reference_to_generated_area_ratio")
    contains_outlet = best_match.get("contains_outlet")
    scope = best_match.get("reference_scope")

    if contains_outlet is False:
        caveats.append("Reference boundary does not contain the modeled outlet.")
    if scope == "regional_context_not_equivalent":
        caveats.append(
            "Reference area is not comparable in scale (ratio outside 0.5-2.0x); "
            "treat as regional context, not a boundary check."
        )
    if iou is None or area_ratio is None:
        return "unknown", caveats or ["Comparison is missing IOU or area-ratio metrics."]
    if iou >= 0.8 and 0.85 <= area_ratio <= 1.15 and contains_outlet is not False:
        return "high", caveats
    if iou >= 0.5 and 0.7 <= area_ratio <= 1.3 and contains_outlet is not False:
        return "moderate", caveats
    caveats.append(
        f"IOU={iou:.2f}, area ratio={area_ratio:.2f} fall outside the moderate-confidence band "
        "(need IOU>=0.5 and ratio in 0.7-1.3x)."
    )
    return "low", caveats


def _reach_confidence(payload: dict[str, Any]) -> tuple[str | None, list[str]]:
    within_pct = payload.get("generated_within_tolerance_pct")
    offset = payload.get("mean_lateral_offset_m")
    tolerance = payload.get("tolerance_m", 30.0)
    if within_pct is None or offset is None:
        return None, []
    if within_pct >= 90 and offset <= tolerance:
        return "high", []
    if within_pct >= 60:
        return "moderate", []
    return "low", [
        f"Only {within_pct:.0f}% of the generated stream network falls within "
        f"{tolerance:g}m of mapped NHD flowlines."
    ]


def _snap_confidence(distance_m: float | None) -> str | None:
    if distance_m is None:
        return None
    if distance_m < SNAP_GREEN_M:
        return "high"
    if distance_m < SNAP_YELLOW_M:
        return "moderate"
    return "low"


def _dem_resolution_m(outputs_dir: Path) -> float | None:
    """Best-effort DEM pixel size read; absence is not an error."""

    candidates = (
        outputs_dir / "clipped" / "cliped_utm_wsclip.tif",
        outputs_dir / "clipped" / "clipped_utm_wsclip.tif",
    )
    dem_path = next((p for p in candidates if p.is_file()), None)
    if dem_path is None:
        return None
    try:
        from osgeo import gdal

        gdal.UseExceptions()
        dataset = gdal.Open(str(dem_path))
        if dataset is None:
            return None
        transform = dataset.GetGeoTransform()
        return abs(float(transform[1]))
    except Exception:
        return None


def build_delineation_confidence_summary(outputs_dir: str | Path) -> dict[str, Any]:
    """Aggregate existing comparison/snap reports into one confidence summary.

    Reads whichever reports already exist under ``outputs_dir`` (written
    earlier in run_full's comparison and DEM-acquisition steps); computes
    nothing new from geometry itself.
    """

    outputs = Path(outputs_dir).expanduser().resolve()
    caveats: list[str] = []
    ratings: list[str] = []

    boundary_comparisons: dict[str, Any] = {}
    for key, filename in BOUNDARY_COMPARISON_REPORTS:
        payload = _load_json(outputs / filename)
        if payload is None:
            continue
        best_match = payload.get("best_match", {})
        reference_role = payload.get("reference_role") or best_match.get(
            "reference_role", "validation"
        )
        informational = reference_role in INFORMATIONAL_REFERENCE_ROLES
        rating, comparison_caveats = _boundary_confidence(best_match)
        displayed_rating = "informational" if informational else rating
        boundary_comparisons[key] = {
            "report": filename,
            "reference_kind": payload.get("reference_kind"),
            "reference_role": reference_role,
            "generated_area_km2": best_match.get("generated_area_km2"),
            "reference_area_km2": best_match.get("reference_area_km2"),
            "reference_to_generated_area_ratio": best_match.get("reference_to_generated_area_ratio"),
            "iou": best_match.get("iou"),
            "boundary_hausdorff_m": best_match.get("boundary_hausdorff_m"),
            "contains_outlet": best_match.get("contains_outlet"),
            "reference_scope": best_match.get("reference_scope"),
            "confidence": displayed_rating,
        }
        if informational:
            caveats.append(
                f"[{key}] The reference is an acquisition estimate; its comparison is "
                "informational and excluded from delineation confidence."
            )
        else:
            ratings.append(rating)
            caveats.extend(f"[{key}] {c}" for c in comparison_caveats)

    reach_summary = None
    reach_payload = _load_json(outputs / REACH_COMPARISON_REPORT)
    if reach_payload is not None:
        rating, reach_caveats = _reach_confidence(reach_payload)
        reach_summary = {
            "report": REACH_COMPARISON_REPORT,
            "mean_lateral_offset_m": reach_payload.get("mean_lateral_offset_m"),
            "hausdorff_distance_m": reach_payload.get("hausdorff_distance_m"),
            "generated_within_tolerance_pct": reach_payload.get("generated_within_tolerance_pct"),
            "reference_within_tolerance_pct": reach_payload.get("reference_within_tolerance_pct"),
            "tolerance_m": reach_payload.get("tolerance_m"),
            "confidence": rating,
        }
        if rating is not None:
            ratings.append(rating)
        caveats.extend(f"[reach] {c}" for c in reach_caveats)

    snap_distance_m = None
    documented_snap_distance_m = None
    dem_summary_payload = _load_json(outputs.parent / DEM_WORKFLOW_SUMMARY) or _load_json(
        outputs / DEM_WORKFLOW_SUMMARY
    )
    outlet_snap = None
    if dem_summary_payload is not None:
        snap_distance_m = dem_summary_payload.get("snap_distance_m")
        documented_snap_distance_m = dem_summary_payload.get("documented_snap_distance_m")
        snap_rating = _snap_confidence(snap_distance_m)
        outlet_snap = {
            "report": DEM_WORKFLOW_SUMMARY,
            "snap_distance_m": snap_distance_m,
            "documented_snap_distance_m": documented_snap_distance_m,
            "documented_snap_was_inside": dem_summary_payload.get("documented_snap_was_inside"),
            "confidence": snap_rating,
            "thresholds_m": {"green_below": SNAP_GREEN_M, "yellow_below": SNAP_YELLOW_M},
        }
        if snap_rating is not None:
            ratings.append(snap_rating)
        if dem_summary_payload.get("documented_snap_was_inside") is False:
            caveats.append(
                "[outlet] The outlet does not fall inside the documented reference boundary."
            )

    dem_resolution_m = _dem_resolution_m(outputs)

    if not boundary_comparisons and reach_summary is None and outlet_snap is None:
        overall = "unknown"
        caveats.append(
            "No watershed/reach comparison or outlet-snap reports were found; run the WBD, "
            "documented-watershed, NHDPlus, reach-network, or DEM-acquisition steps before "
            "treating this delineation as production-ready."
        )
    elif "low" in ratings or "unknown" in ratings or not ratings:
        overall = "low" if ratings else "unknown"
    elif "moderate" in ratings:
        overall = "moderate"
    else:
        overall = "high"

    return {
        "schema_name": "WatershedDelineationConfidenceSummary",
        "schema_version": "1.0",
        "overall_confidence": overall,
        "outlet_snap": outlet_snap,
        "boundary_comparisons": boundary_comparisons,
        "reach_comparison": reach_summary,
        "dem_resolution_m": dem_resolution_m,
        "caveats": caveats,
        "interpretation": (
            "This aggregates GIStoOHQ's own comparison and outlet-snap reports into one summary; "
            "it does not independently verify the delineation and is only as good as the reference "
            "data available for this site. Small catchments (under roughly 100 km^2) are the hardest "
            "case for automated delineation in general - see e.g. BasinKit's published validation "
            "reporting a 181% median area error at that scale with its default backend "
            "(github.com/Praddy-GByte/basinkit). Treat 'high' confidence here as relative to "
            "GIStoOHQ's own checks, not as an absolute guarantee; verify against authoritative "
            "hydrography before design use."
        ),
    }


def write_delineation_confidence_summary(outputs_dir: str | Path) -> Path:
    """Build and write the summary as ``<outputs_dir>/watershed_delineation_confidence.json``."""

    outputs = Path(outputs_dir).expanduser().resolve()
    summary = build_delineation_confidence_summary(outputs)
    target = outputs / "watershed_delineation_confidence.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return target

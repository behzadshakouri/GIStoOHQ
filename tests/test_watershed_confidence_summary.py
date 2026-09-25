import json

from ohqbuilder.watershed_confidence_summary import (
    build_delineation_confidence_summary,
    write_delineation_confidence_summary,
)


def _write_comparison(outputs, filename, *, iou, area_ratio, contains_outlet=True, scope="comparable_scale"):
    payload = {
        "reference_kind": "wbd_huc12",
        "best_match": {
            "generated_area_km2": 23.75,
            "reference_area_km2": 23.75 * area_ratio,
            "reference_to_generated_area_ratio": area_ratio,
            "iou": iou,
            "boundary_hausdorff_m": 42.0,
            "contains_outlet": contains_outlet,
            "reference_scope": scope,
        },
    }
    (outputs / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_no_reports_present_is_unknown_not_silently_high(tmp_path):
    summary = build_delineation_confidence_summary(tmp_path)
    assert summary["overall_confidence"] == "unknown"
    assert summary["boundary_comparisons"] == {}
    assert summary["reach_comparison"] is None
    assert any("No watershed/reach comparison" in c for c in summary["caveats"])


def test_high_agreement_boundary_comparison_is_high_confidence(tmp_path):
    _write_comparison(tmp_path, "watershed_wbd_comparison.json", iou=0.92, area_ratio=1.02)
    summary = build_delineation_confidence_summary(tmp_path)
    assert summary["boundary_comparisons"]["wbd"]["confidence"] == "high"
    assert summary["overall_confidence"] == "high"


def test_poor_iou_or_missing_outlet_is_low_confidence_with_caveat(tmp_path):
    _write_comparison(
        tmp_path, "watershed_documented_comparison.json",
        iou=0.2, area_ratio=1.8, contains_outlet=False,
    )
    summary = build_delineation_confidence_summary(tmp_path)
    assert summary["boundary_comparisons"]["documented"]["confidence"] == "low"
    assert summary["overall_confidence"] == "low"
    assert any("does not contain the modeled outlet" in c for c in summary["caveats"])


def test_mixed_high_and_moderate_reports_take_the_lower_overall_rating(tmp_path):
    _write_comparison(tmp_path, "watershed_wbd_comparison.json", iou=0.92, area_ratio=1.02)
    _write_comparison(tmp_path, "watershed_documented_comparison.json", iou=0.55, area_ratio=1.2)
    summary = build_delineation_confidence_summary(tmp_path)
    assert summary["boundary_comparisons"]["wbd"]["confidence"] == "high"
    assert summary["boundary_comparisons"]["documented"]["confidence"] == "moderate"
    assert summary["overall_confidence"] == "moderate"


def test_outlet_snap_distance_classified_against_green_yellow_red_thresholds(tmp_path):
    intermediate = tmp_path / "intermediate"
    intermediate.mkdir()
    (intermediate / "dem_workflow_summary.json").write_text(
        json.dumps({"snap_distance_m": 12.0, "documented_snap_was_inside": True}),
        encoding="utf-8",
    )
    summary = build_delineation_confidence_summary(tmp_path)
    assert summary["outlet_snap"]["snap_distance_m"] == 12.0
    assert summary["outlet_snap"]["confidence"] == "high"

    (intermediate / "dem_workflow_summary.json").write_text(
        json.dumps({"snap_distance_m": 120.0, "documented_snap_was_inside": True}),
        encoding="utf-8",
    )
    summary = build_delineation_confidence_summary(tmp_path)
    assert summary["outlet_snap"]["confidence"] == "low"
    assert summary["overall_confidence"] == "low"


def test_write_delineation_confidence_summary_writes_expected_file(tmp_path):
    _write_comparison(tmp_path, "watershed_wbd_comparison.json", iou=0.92, area_ratio=1.02)
    target = write_delineation_confidence_summary(tmp_path)
    assert target == tmp_path / "watershed_delineation_confidence.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_name"] == "WatershedDelineationConfidenceSummary"
    assert payload["overall_confidence"] == "high"

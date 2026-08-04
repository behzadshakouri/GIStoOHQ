import json

import pytest

from ohqbuilder.report_baseline import (
    ReportBaselineError,
    compare_report_baseline,
    create_report_baseline,
)


def write_report(outputs, name, payload):
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / name).write_text(json.dumps(payload), encoding="utf-8")


def test_report_baseline_ignores_paths_and_compares_stable_metrics(tmp_path):
    outputs = tmp_path / "outputs"
    report = {
        "status": "pass",
        "output_path": "/machine/one/subwatersheds.gpkg",
        "gap_m2": 3748.0,
        "subwatersheds": [{"id": "1", "incremental_area_km2": 4.1498}],
    }
    write_report(outputs, "subwatershed_partition_report.json", report)
    baseline = create_report_baseline(outputs, tmp_path / "baseline.json")

    report["output_path"] = "/machine/two/subwatersheds.gpkg"
    report["gap_m2"] = 3748.4
    write_report(outputs, "subwatershed_partition_report.json", report)

    result = compare_report_baseline(outputs, baseline, absolute_tolerance=0.5)
    assert result.passed
    assert result.differences == ()


def test_report_baseline_reports_metric_regressions_and_missing_reports(tmp_path):
    outputs = tmp_path / "outputs"
    write_report(outputs, "reaches_nhd_comparison.json", {"generated_near_nhd_pct": 98.4})
    baseline = create_report_baseline(outputs, tmp_path / "baseline.json")
    write_report(outputs, "reaches_nhd_comparison.json", {"generated_near_nhd_pct": 91.0})

    result = compare_report_baseline(outputs, baseline, absolute_tolerance=0.1)
    assert not result.passed
    assert result.differences[0]["path"].endswith("generated_near_nhd_pct")

    (outputs / "reaches_nhd_comparison.json").unlink()
    missing = compare_report_baseline(outputs, baseline)
    assert not missing.passed
    assert any("reaches_nhd_comparison.json" in item["path"] for item in missing.differences)


def test_report_baseline_requires_at_least_one_supported_report(tmp_path):
    with pytest.raises(ReportBaselineError, match="No supported JSON reports"):
        create_report_baseline(tmp_path, tmp_path / "baseline.json")

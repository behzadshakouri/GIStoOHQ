import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ohqbuilder.full_runner import (
    acquisition_bounds,
    bounds_covering_outlet,
    buffer_covering_bounds,
    existing_legacy_hms_project,
    full_run_summary,
    network_element_counts,
    run_full_pipeline,
    write_watershed_report,
)
from ohqbuilder.legacy_inputs import LegacyInputWorkflowError, verify_reach_writer_revision


@pytest.fixture(autouse=True)
def stub_watershed_builder(monkeypatch):
    monkeypatch.setattr(
        "ohqbuilder.full_runner.WatershedBuilder",
        lambda settings: SimpleNamespace(
            build=lambda: SimpleNamespace(subbasins=[], reaches=[], junctions=[])
        ),
    )


def test_full_run_summary_reports_metrics_and_artifacts(tmp_path):
    watershed = SimpleNamespace(
        subbasins=[SimpleNamespace(area_km2=0.04), SimpleNamespace(area_km2=0.02)],
        reaches=[object(), object(), object()],
        junctions=[object(), object()],
        topology=[
            SimpleNamespace(element_type="subbasin"),
            SimpleNamespace(element_type="subbasin"),
            SimpleNamespace(element_type="reach"),
            SimpleNamespace(element_type="junction"),
            SimpleNamespace(element_type="sink"),
        ],
    )

    summary = full_run_summary(watershed, tmp_path / "site.ohq", tmp_path / "site.hms")

    assert "Watershed Area\n  0.0600 km²" in summary
    assert "GIS Extraction\n  Subbasins : 2\n  Reaches   : 3\n  Junctions : 2" in summary
    assert "Final Model Network\n  Subbasins : 2\n  Reaches   : 1\n  Junctions : 1" in summary
    assert "✓ OHQ model" in summary
    assert "✓ HEC-HMS project" in summary
    assert str((tmp_path / "site.ohq").resolve()) in summary


def test_reach_writer_revision_detects_stale_legacy_script(tmp_path):
    script = tmp_path / "extract_reaches.py"
    script.write_text('raise Exception("reaches.gpkg was written but is invalid")\n')

    with pytest.raises(LegacyInputWorkflowError, match="different revisions"):
        verify_reach_writer_revision(tmp_path)


def test_reach_writer_revision_accepts_current_legacy_script():
    script = verify_reach_writer_revision()

    assert script.name == "extract_reaches.py"


def test_full_run_summary_reports_authoritative_boundary_metrics(tmp_path):
    comparison = tmp_path / "watershed_wbd_comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "reference_layer": "WBDHU12_reference",
                "best_match": {
                    "reference_id": "020700100101",
                    "generated_area_km2": 38.3443,
                    "reference_area_km2": 40.0,
                    "iou": 0.8123,
                    "omission_area_km2": 3.2,
                    "commission_area_km2": 1.5,
                    "boundary_hausdorff_m": 87.5,
                },
            }
        ),
        encoding="utf-8",
    )
    watershed = SimpleNamespace(subbasins=[], reaches=[], junctions=[])

    summary = full_run_summary(
        watershed,
        tmp_path / "site.ohq",
        tmp_path / "site.hms",
        comparison_paths=[comparison],
    )

    assert "Boundary Comparison (WBDHU12_reference)" in summary
    assert "Reference ID       : 020700100101" in summary
    assert "Area difference    : -4.14%" in summary
    assert "Intersection/Union : 0.812" in summary
    assert "Boundary Hausdorff : 87.5 m" in summary


def test_full_run_summary_reports_nhd_reach_alignment(tmp_path):
    comparison = tmp_path / "reaches_nhd_comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "tolerance_m": 30.0,
                "generated_length_km": 42.1,
                "reference_length_km": 45.2,
                "generated_within_tolerance_pct": 91.2,
                "reference_within_tolerance_pct": 84.3,
                "mean_lateral_offset_m": 12.6,
                "hausdorff_distance_m": 125.4,
            }
        ),
        encoding="utf-8",
    )

    summary = full_run_summary(
        SimpleNamespace(subbasins=[], reaches=[], junctions=[]),
        tmp_path / "site.ohq",
        tmp_path / "site.hms",
        reach_comparison_paths=[comparison],
    )

    assert "Reach Network Comparison (NHD)" in summary
    assert "Generated near NHD  : 91.2%" in summary
    assert "NHD near generated  : 84.3%" in summary
    assert "Mean lateral offset : 12.6 m" in summary
    assert "Network Hausdorff   : 125.4 m" in summary


def test_watershed_report_contains_parameters_and_artifacts(tmp_path):
    watershed = SimpleNamespace(
        subbasins=[
            SimpleNamespace(
                name="Subbasin_1",
                area_km2=0.0638,
                curve_number=87.3,
                slope_pct=6.3,
                flow_len_ft=1517,
                tc_min=13.8,
                lag_min=8.3,
            )
        ],
        reaches=[object(), object()],
        junctions=[object()],
        topology=[
            SimpleNamespace(element_type="subbasin"),
            SimpleNamespace(element_type="reach"),
            SimpleNamespace(element_type="sink"),
        ],
    )

    report = write_watershed_report(watershed, tmp_path / "site.ohq", tmp_path / "site.hms")
    content = report.read_text(encoding="utf-8")

    assert report == tmp_path / "watershed_report.html"
    assert "0.0638 km²" in content
    assert "Subbasin_1" in content
    assert ">1517<" in content
    assert ">13.8<" in content
    assert str(tmp_path / "site.hms") in content
    assert "GREEN: less than 20 m" in content
    assert "YELLOW: 20–75 m" in content
    assert "RED: greater than 75 m" in content
    assert "<h2>GIS Extraction</h2>" in content
    assert "<li>Reaches: 2</li><li>Junctions: 1</li>" in content
    assert "<h2>Final Model Network</h2>" in content
    assert "<li>Reaches: 1</li><li>Junctions: 0</li>" in content


def test_watershed_report_includes_reference_comparisons(tmp_path):
    comparison = tmp_path / "watershed_nhdplus_comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "reference_layer": "upstream_boundary",
                "disagreement_geopackage": str(tmp_path / "disagreement.gpkg"),
                "best_match": {
                    "reference_id": "reach-42",
                    "iou": 0.8123,
                    "omission_area_km2": 1.2,
                    "commission_area_km2": 0.4,
                    "boundary_hausdorff_m": 87.5,
                },
            }
        ),
        encoding="utf-8",
    )
    watershed = SimpleNamespace(subbasins=[], reaches=[], junctions=[])

    report = write_watershed_report(
        watershed,
        tmp_path / "site.ohq",
        tmp_path / "site.hms",
        comparison_paths=[comparison],
    )

    content = report.read_text(encoding="utf-8")
    assert "Boundary comparisons" in content
    assert "upstream_boundary" in content
    assert "reach-42" in content
    assert ">0.812<" in content
    assert "disagreement.gpkg" in content


def test_watershed_report_includes_reach_network_comparison(tmp_path):
    comparison = tmp_path / "reaches_nhd_comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "tolerance_m": 30.0,
                "generated_length_km": 42.1,
                "reference_length_km": 45.2,
                "generated_within_tolerance_pct": 91.2,
                "reference_within_tolerance_pct": 84.3,
                "mean_lateral_offset_m": 12.6,
                "hausdorff_distance_m": 125.4,
            }
        ),
        encoding="utf-8",
    )
    report = write_watershed_report(
        SimpleNamespace(subbasins=[], reaches=[], junctions=[]),
        tmp_path / "site.ohq",
        tmp_path / "site.hms",
        reach_comparison_paths=[comparison],
    )

    content = report.read_text(encoding="utf-8")
    assert "Reach-network comparison" in content
    assert ">91.2%<" in content
    assert ">84.3%<" in content
    assert ">12.6<" in content
    assert ">125.4<" in content


def test_network_counts_fall_back_to_extracted_when_topology_is_unavailable():
    watershed = SimpleNamespace(subbasins=[1], reaches=[1, 2], junctions=[1])

    assert network_element_counts(watershed) == {
        "subbasin": (1, 1),
        "reach": (2, 2),
        "junction": (1, 1),
    }


def test_existing_legacy_hms_project_prefers_complete_phase2_output(tmp_path):
    project = tmp_path / "WS3_HMS" / "SITE_A" / "SITE_A.hms"
    project.parent.mkdir(parents=True)
    project.write_text("Project: SITE_A\n", encoding="utf-8")

    # CLI argparse supplies root as a string; retain Path support for Python callers.
    assert existing_legacy_hms_project(str(tmp_path), "SITE_A") == project.resolve()
    assert existing_legacy_hms_project(tmp_path, "SITE_A") == project.resolve()
    assert existing_legacy_hms_project(tmp_path, "MISSING") is None


def test_bounds_covering_outlet_expands_area_with_routing_safety_margin():
    original = (-77.01, 38.99, -77.00, 39.00)

    expanded = bounds_covering_outlet(original, -76.98, 39.01, margin_m=500)

    assert expanded[0:2] == original[0:2]
    assert expanded[2] > -76.98
    assert expanded[3] > 39.01


def test_full_pipeline_runs_every_stage(monkeypatch, tmp_path):
    calls = []
    download_options = {}
    phase_options = {}
    downloads = tmp_path / "downloads"

    def fake_download(*args, **kwargs):
        download_options.update(kwargs)
        calls.append("download-all")
        return SimpleNamespace(
            download_dir=downloads,
        )

    monkeypatch.setattr(
        "ohqbuilder.full_runner.download_all_inputs",
        fake_download,
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.materialize_source_inputs",
        lambda *args, **kwargs: calls.append("materialize"),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.run_hydrology_preprocessing",
        lambda *args, **kwargs: calls.append("routing"),
    )
    def fake_legacy(*args, **kwargs):
        phase_options["options"] = args[4]
        calls.append("phases")

    monkeypatch.setattr("ohqbuilder.full_runner.run_legacy_input_workflow", fake_legacy)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.InputValidator",
        lambda: SimpleNamespace(
            validate=lambda settings: (
                calls.append("validate") or SimpleNamespace(ok=True, errors=[])
            )
        ),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_ohq_project",
        lambda *args, **kwargs: calls.append("build") or str(tmp_path / "SITE_A.ohq"),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_hms_project",
        lambda *args, **kwargs: (
            calls.append("build-hms") or SimpleNamespace(project_file=tmp_path / "SITE_A.hms")
        ),
    )

    result = run_full_pipeline(
        str(tmp_path),
        "SITE_A",
        lon=-111.2,
        lat=34.1,
        site_id="source-id",
        download_dir=downloads,
        max_tiles=5,
        soil_pixel_size=0.0002,
        soil_top_depth=15,
    )

    assert calls == [
        "download-all",
        "materialize",
        "routing",
        "phases",
        "validate",
        "build",
        "build-hms",
    ]
    assert callable(download_options.pop("progress"))
    assert download_options == {
        "lon": -111.2,
        "lat": 34.1,
        "site_id": "source-id",
        "download_dir": downloads,
        "buffer_m": 5000.0,
        "max_tiles": 5,
        "max_file_size_mb": None,
        "soil_pixel_size": 0.0002,
        "soil_top_depth": 15,
        "use_existing_outlet": False,
    }
    assert result.output_path == Path(tmp_path / "SITE_A.ohq")
    assert result.hms_project_path == tmp_path / "SITE_A.hms"
    assert result.report_path == tmp_path / "watershed_report.html"
    assert phase_options["options"].refresh_auto_pour_points is True
    assert phase_options["options"].child_options == {"MAX_OUTLET_SNAP_M": 50.0}


def test_full_pipeline_reports_cli_outlet_recreation(monkeypatch, tmp_path):
    messages = []
    monkeypatch.setattr(
        "ohqbuilder.full_runner.download_all_inputs",
        lambda *a, **k: SimpleNamespace(download_dir=tmp_path / "downloads"),
    )
    monkeypatch.setattr("ohqbuilder.full_runner.materialize_source_inputs", lambda *a, **k: None)
    monkeypatch.setattr("ohqbuilder.full_runner.run_hydrology_preprocessing", lambda *a, **k: None)
    monkeypatch.setattr("ohqbuilder.full_runner.run_legacy_input_workflow", lambda *a, **k: None)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.InputValidator",
        lambda: SimpleNamespace(validate=lambda settings: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_ohq_project", lambda *a, **k: tmp_path / "result.ohq"
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_hms_project",
        lambda *a, **k: SimpleNamespace(project_file=tmp_path / "result.hms"),
    )

    run_full_pipeline(tmp_path, "SITE", lon=-77.0, lat=39.0, progress=messages.append)

    assert "Outlet source: CLI longitude/latitude (outlet.shp will be recreated)" in messages


def test_full_pipeline_requires_and_preserves_reviewed_pour_points(monkeypatch, tmp_path):
    outputs = tmp_path / "SITE_A" / "outputs"
    outputs.mkdir(parents=True)
    reviewed = outputs / "pour_points.shp"
    reviewed.write_text("reviewed", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(
        "ohqbuilder.full_runner.download_all_inputs",
        lambda *a, **k: SimpleNamespace(download_dir=tmp_path / "downloads"),
    )
    monkeypatch.setattr("ohqbuilder.full_runner.materialize_source_inputs", lambda *a, **k: None)
    monkeypatch.setattr("ohqbuilder.full_runner.run_hydrology_preprocessing", lambda *a, **k: None)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.run_legacy_input_workflow",
        lambda *args, **kwargs: captured.setdefault("options", args[4]),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.InputValidator",
        lambda: SimpleNamespace(validate=lambda settings: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_ohq_project", lambda *a, **k: tmp_path / "SITE_A.ohq"
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_hms_project",
        lambda *a, **k: SimpleNamespace(project_file=tmp_path / "SITE_A.hms"),
    )

    run_full_pipeline(
        tmp_path, "SITE_A", lon=-77.0, lat=39.0, use_reviewed_pour_points=True
    )

    assert captured["options"].auto_pour_points is False
    assert captured["options"].refresh_auto_pour_points is False
    assert reviewed.read_text(encoding="utf-8") == "reviewed"


def test_full_pipeline_uses_drawn_area_for_download_coverage_and_clipping(monkeypatch, tmp_path):
    area = tmp_path / "area.geojson"
    area.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":'
        '{"type":"Polygon","coordinates":[[[-77.1,38.9],[-76.9,38.9],'
        '[-76.9,39.1],[-77.1,39.1],[-77.1,38.9]]]},"properties":{}}]}',
        encoding="utf-8",
    )
    calls = {}

    def fake_download(*args, **kwargs):
        calls["download"] = kwargs
        return SimpleNamespace(download_dir=tmp_path / "downloads")

    monkeypatch.setattr("ohqbuilder.full_runner.download_all_inputs", fake_download)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.materialize_source_inputs",
        lambda *args, **kwargs: calls.setdefault("materialize", kwargs),
    )
    monkeypatch.setattr("ohqbuilder.full_runner.run_hydrology_preprocessing", lambda *a, **k: None)
    monkeypatch.setattr("ohqbuilder.full_runner.run_legacy_input_workflow", lambda *a, **k: None)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.InputValidator",
        lambda: SimpleNamespace(validate=lambda settings: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_ohq_project", lambda *a, **k: tmp_path / "result.ohq"
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_hms_project",
        lambda *a, **k: SimpleNamespace(project_file=tmp_path / "result.hms"),
    )

    run_full_pipeline(tmp_path, "SITE", lon=-77.0, lat=39.0, acquisition_area=area)

    assert acquisition_bounds(area) == (-77.1, 38.9, -76.9, 39.1)
    assert calls["download"]["buffer_m"] >= buffer_covering_bounds(
        -77.0, 39.0, (-77.1, 38.9, -76.9, 39.1)
    )
    assert calls["materialize"]["clip_bounds"] == (-77.1, 38.9, -76.9, 39.1)


def test_full_pipeline_expands_drawn_area_that_excludes_outlet(monkeypatch, tmp_path):
    area = tmp_path / "area.geojson"
    area.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":'
        '{"type":"Polygon","coordinates":[[[-77.1,38.9],[-77.0,38.9],'
        '[-77.0,39.0],[-77.1,39.0],[-77.1,38.9]]]},"properties":{}}]}',
        encoding="utf-8",
    )
    calls = {}
    def download(*args, **kwargs):
        calls["download"] = kwargs
        return SimpleNamespace(download_dir=tmp_path / "downloads")

    monkeypatch.setattr("ohqbuilder.full_runner.download_all_inputs", download)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.materialize_source_inputs",
        lambda *a, **kwargs: calls.setdefault("materialize", kwargs),
    )
    monkeypatch.setattr("ohqbuilder.full_runner.run_hydrology_preprocessing", lambda *a, **k: None)
    monkeypatch.setattr("ohqbuilder.full_runner.run_legacy_input_workflow", lambda *a, **k: None)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.InputValidator",
        lambda: SimpleNamespace(validate=lambda settings: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_ohq_project", lambda *a, **k: tmp_path / "result.ohq"
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_hms_project",
        lambda *a, **k: SimpleNamespace(project_file=tmp_path / "result.hms"),
    )

    run_full_pipeline(tmp_path, "SITE", lon=-76.98, lat=39.01, acquisition_area=area)

    minx, miny, maxx, maxy = calls["materialize"]["clip_bounds"]
    assert (minx, miny) == (-77.1, 38.9)
    assert maxx > -76.98
    assert maxy > 39.01


def test_full_pipeline_preserves_area_that_already_contains_outlet(monkeypatch, tmp_path):
    area = tmp_path / "area.geojson"
    area.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":'
        '{"type":"Polygon","coordinates":[[[-77.002,38.998],[-76.998,38.998],'
        '[-76.998,39.002],[-77.002,39.002],[-77.002,38.998]]]},"properties":{}}]}',
        encoding="utf-8",
    )
    calls = {}

    def download(*args, **kwargs):
        calls["download"] = kwargs
        return SimpleNamespace(download_dir=tmp_path / "downloads")
    monkeypatch.setattr("ohqbuilder.full_runner.download_all_inputs", download)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.materialize_source_inputs",
        lambda *a, **kwargs: calls.setdefault("materialize", kwargs),
    )
    monkeypatch.setattr("ohqbuilder.full_runner.run_hydrology_preprocessing", lambda *a, **k: None)
    monkeypatch.setattr("ohqbuilder.full_runner.run_legacy_input_workflow", lambda *a, **k: None)
    monkeypatch.setattr(
        "ohqbuilder.full_runner.InputValidator",
        lambda: SimpleNamespace(validate=lambda settings: SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr("ohqbuilder.full_runner.build_ohq_project", lambda *a, **k: tmp_path / "x.ohq")
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_hms_project",
        lambda *a, **k: SimpleNamespace(project_file=tmp_path / "x.hms"),
    )

    run_full_pipeline(tmp_path, "SITE", lon=-77.0, lat=39.0, acquisition_area=area)

    assert calls["materialize"]["clip_bounds"] == (-77.002, 38.998, -76.998, 39.002)
    assert calls["download"]["buffer_m"] < 500.0

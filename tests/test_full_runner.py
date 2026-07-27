from pathlib import Path
from types import SimpleNamespace

from ohqbuilder.full_runner import (
    acquisition_bounds,
    bounds_covering_outlet,
    buffer_covering_bounds,
    run_full_pipeline,
)


def test_bounds_covering_outlet_expands_area_with_routing_safety_margin():
    original = (-77.01, 38.99, -77.00, 39.00)

    expanded = bounds_covering_outlet(original, -76.98, 39.01, margin_m=500)

    assert expanded[0:2] == original[0:2]
    assert expanded[2] > -76.98
    assert expanded[3] > 39.01


def test_full_pipeline_runs_every_stage(monkeypatch, tmp_path):
    calls = []
    download_options = {}
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
    monkeypatch.setattr(
        "ohqbuilder.full_runner.run_legacy_input_workflow",
        lambda *args, **kwargs: calls.append("phases"),
    )
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
        tmp_path,
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
    }
    assert result.output_path == Path(tmp_path / "SITE_A.ohq")
    assert result.hms_project_path == tmp_path / "SITE_A.hms"


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

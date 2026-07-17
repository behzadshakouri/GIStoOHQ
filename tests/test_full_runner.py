from pathlib import Path
from types import SimpleNamespace

from ohqbuilder.full_runner import run_full_pipeline


def test_full_pipeline_runs_every_stage(monkeypatch, tmp_path):
    calls = []
    downloads = tmp_path / "downloads"
    dem = tmp_path / "dem.tif"
    monkeypatch.setattr("ohqbuilder.full_runner.find_demcheck", lambda path=None: None)

    monkeypatch.setattr(
        "ohqbuilder.full_runner.fetch_phase1_inputs",
        lambda *args, **kwargs: calls.append("fetch") or SimpleNamespace(download_dir=downloads),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.materialize_dem",
        lambda *args, **kwargs: calls.append("dem") or SimpleNamespace(output_path=dem),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.materialize_flowlines",
        lambda *args, **kwargs: calls.append("hydro"),
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
        lambda: SimpleNamespace(validate=lambda settings: calls.append("validate") or SimpleNamespace(ok=True, errors=[])),
    )
    monkeypatch.setattr(
        "ohqbuilder.full_runner.build_ohq_project",
        lambda *args, **kwargs: calls.append("build") or str(tmp_path / "SITE_A.ohq"),
    )

    result = run_full_pipeline(tmp_path, "SITE_A", lon=-111.2, lat=34.1)

    assert calls == ["fetch", "dem", "hydro", "routing", "phases", "validate", "build"]
    assert result.output_path == Path(tmp_path / "SITE_A.ohq")

from pathlib import Path
from types import SimpleNamespace

from ohqbuilder.full_runner import run_full_pipeline


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

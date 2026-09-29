def test_import_pipeline():
    from ohqbuilder.pipeline import build_ohq_project

    assert build_ohq_project


def test_pipeline_writes_legacy_mixed_and_standard_hru_files(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ohqbuilder.pipeline import build_ohq_project

    watershed = SimpleNamespace(summary=lambda: "test watershed")
    monkeypatch.delenv("OHQ_CHANNEL_PROFILES_FILE", raising=False)
    monkeypatch.delenv("OHQ_TEMPERATURE_FILE", raising=False)
    settings = SimpleNamespace(
        project_name="Test",
        paths=SimpleNamespace(outputs_path=tmp_path),
        ohq=SimpleNamespace(include_comments=False),
    )
    monkeypatch.setattr(
        "ohqbuilder.pipeline.WatershedBuilder",
        lambda _settings: SimpleNamespace(build=lambda: watershed),
    )
    monkeypatch.setattr(
        "ohqbuilder.pipeline.retain_topology_elements", lambda _ws: None
    )
    monkeypatch.setattr(
        "ohqbuilder.pipeline.TopologyValidator.validate", lambda _self, _ws: None
    )
    monkeypatch.setattr(
        "ohqbuilder.pipeline.ParameterValidator.validate", lambda _self, _ws: None
    )

    writes = []

    class Writer:
        def __init__(self, include_comments, formulation):
            self.formulation = formulation

        def write(self, _watershed, path):
            writes.append((self.formulation, path))

    monkeypatch.setattr("ohqbuilder.pipeline.OHQWriter", Writer)

    result = build_ohq_project(settings, tmp_path / "model.ohq")

    assert result == str(tmp_path / "model_legacy.ohq")
    assert writes == [
        ("legacy", tmp_path / "model_legacy.ohq"),
        ("mixed_hru", tmp_path / "model_mixed_hru.ohq"),
        ("standard_hru", tmp_path / "model_standard_hru.ohq"),
    ]


def test_pipeline_skips_legacy_cn_when_snow_is_enabled(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ohqbuilder.pipeline import build_ohq_project

    watershed = SimpleNamespace(summary=lambda: "test watershed")
    monkeypatch.setenv("OHQ_TEMPERATURE_FILE", "/data/temperature.txt")
    monkeypatch.delenv("OHQ_CHANNEL_PROFILES_FILE", raising=False)
    settings = SimpleNamespace(
        project_name="Test",
        paths=SimpleNamespace(outputs_path=tmp_path),
        ohq=SimpleNamespace(include_comments=False),
    )
    monkeypatch.setattr(
        "ohqbuilder.pipeline.WatershedBuilder",
        lambda _settings: SimpleNamespace(build=lambda: watershed),
    )
    monkeypatch.setattr("ohqbuilder.pipeline.retain_topology_elements", lambda _ws: None)
    monkeypatch.setattr("ohqbuilder.pipeline.TopologyValidator.validate", lambda _self, _ws: None)
    monkeypatch.setattr("ohqbuilder.pipeline.ParameterValidator.validate", lambda _self, _ws: None)

    writes = []

    class Writer:
        def __init__(self, include_comments, formulation):
            self.formulation = formulation

        def write(self, _watershed, path):
            writes.append((self.formulation, path))

    monkeypatch.setattr("ohqbuilder.pipeline.OHQWriter", Writer)

    stale_legacy = tmp_path / "model_legacy.ohq"
    stale_legacy.write_text("stale snow model")
    result = build_ohq_project(settings, tmp_path / "model.ohq")

    assert result == str(tmp_path / "model_mixed_hru.ohq")
    assert not stale_legacy.exists()
    assert writes == [
        ("mixed_hru", tmp_path / "model_mixed_hru.ohq"),
        ("standard_hru", tmp_path / "model_standard_hru.ohq"),
    ]

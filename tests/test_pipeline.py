def test_import_pipeline():
    from ohqbuilder.pipeline import build_ohq_project

    assert build_ohq_project


def test_pipeline_writes_legacy_and_mixed_hru_files(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ohqbuilder.pipeline import build_ohq_project

    watershed = SimpleNamespace(summary=lambda: "test watershed")
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
    ]

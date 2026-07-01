from types import SimpleNamespace

from ohqbuilder.settings import BuilderSettings
from ohqbuilder.validation.input_validator import InputValidator


def _settings(tmp_path):
    root = tmp_path / "root"
    site = "WS3_GIS/AZ12-100"
    return BuilderSettings.from_args(str(root), site)


def _touch_required_files(settings):
    out = settings.paths.outputs_path
    out.mkdir(parents=True)
    for name in (
        settings.paths.topology,
        settings.paths.subbasins,
        settings.paths.reaches,
        settings.paths.junctions,
    ):
        (out / name).write_text("placeholder", encoding="utf-8")


def test_input_validator_reports_missing_required_files(tmp_path):
    result = InputValidator().validate(_settings(tmp_path), check_schema=False)

    assert not result.ok
    assert len(result.errors) == 4
    assert any("Missing topology input" in error for error in result.errors)


def test_input_validator_skips_schema_when_requested(tmp_path):
    settings = _settings(tmp_path)
    _touch_required_files(settings)

    result = InputValidator(reader=lambda *args, **kwargs: None).validate(settings, check_schema=False)

    assert result.ok
    assert result.errors == []


def test_input_validator_reports_missing_schema_fields(tmp_path):
    settings = _settings(tmp_path)
    _touch_required_files(settings)

    def reader(*args, **kwargs):
        return SimpleNamespace(columns=["id"])

    result = InputValidator(reader=reader).validate(settings)

    assert not result.ok
    assert any("topology input" in error and "ds_name" in error for error in result.errors)
    assert any("reaches input" in error and "reach_id" in error for error in result.errors)


def test_input_validator_accepts_required_schema_fields(tmp_path):
    settings = _settings(tmp_path)
    _touch_required_files(settings)

    def reader(path, layer=None):
        if layer == "topology":
            columns = ["element_id", "element_type", "name", "ds_type", "ds_id", "ds_name"]
        elif layer == "subwatershed_params":
            columns = ["id", "area_km2", "CN", "slope_pct", "flow_len_ft", "tc_min", "lag_min"]
        elif layer == "junctions":
            columns = ["junction_id", "x", "y"]
        else:
            columns = ["reach_id", "length_m", "slope_mm", "base_w_m", "side_z", "manning_n"]
        return SimpleNamespace(columns=columns)

    result = InputValidator(reader=reader).validate(settings)

    assert result.ok
    assert result.errors == []


def test_input_validation_result_to_dict():
    settings = _settings(__import__("pathlib").Path("/tmp"))
    result = InputValidator().validate(settings, check_schema=False)

    data = result.to_dict()

    assert data["ok"] is False
    assert data["errors"]
    assert data["warnings"] == []

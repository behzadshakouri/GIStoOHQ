from pathlib import Path

from ohqbuilder.settings import BuilderSettings


def test_settings_from_args_uses_site_basename_for_project_name():
    settings = BuilderSettings.from_args("/tmp/root", "WS3_GIS/AZ12-100")

    assert settings.project_name == "AZ12_100"
    assert settings.paths.outputs_path == Path("/tmp/root/WS3_GIS/AZ12-100/outputs")


def test_settings_project_name_override():
    settings = BuilderSettings.from_args(
        "/tmp/root",
        "WS3_GIS/AZ12-100",
        project_name="Custom",
    )

    assert settings.project_name == "Custom"

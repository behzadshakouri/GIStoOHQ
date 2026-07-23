from pathlib import Path

import pytest

from ohqbuilder.ui.launcher import (
    LauncherError,
    LauncherState,
    command_for_step,
    geojson_preview_summary,
    load_project_config,
    save_project_config,
    state_from_config,
    update_config_from_state,
)


def test_command_for_prepare_dem():
    command = command_for_step("prepare-dem", LauncherState(config_path=Path("config.yaml")))

    assert command.argv == ("ohqbuild", "prepare-dem", "--config", "config.yaml")


def test_command_for_materialize_inputs_includes_manifest():
    command = command_for_step(
        "materialize-inputs",
        LauncherState(
            config_path=Path("config.yaml"),
            manifest_path=Path("manifest.json"),
            root=Path("."),
            site="SligoCreek",
            source_dir=Path("source_downloads"),
            target_crs="EPSG:26918",
        ),
    )

    assert command.argv == (
        "ohqbuild",
        "materialize-inputs",
        "--root",
        ".",
        "--site",
        "SligoCreek",
        "--source-dir",
        "source_downloads",
        "--target-crs",
        "EPSG:26918",
        "--dem-manifest",
        "manifest.json",
    )


def test_download_dem_manifest_requires_paths():
    with pytest.raises(LauncherError, match="Manifest path"):
        command_for_step("download-dem-manifest", LauncherState(config_path=Path("config.yaml")))


def test_ui_config_load_state_update_and_save(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
site:
  name: SligoCreek
  target_crs: EPSG:26918
dem_acquisition:
  tile_manifest: intermediate/manifest.json
paths:
  raw_dem_dir: dem/raw
""".strip(),
        encoding="utf-8",
    )

    config = load_project_config(config_path)
    state = state_from_config(config_path, config)
    assert state.site == "SligoCreek"
    assert state.target_crs == "EPSG:26918"
    assert state.manifest_path == tmp_path / "intermediate" / "manifest.json"

    updated = update_config_from_state(config, state)
    output = tmp_path / "saved.json"
    save_project_config(output, updated)
    assert load_project_config(output)["site"]["name"] == "SligoCreek"


def test_geojson_preview_summary(tmp_path):
    geojson = tmp_path / "area.geojson"
    geojson.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[]},"properties":{}}]}',
        encoding="utf-8",
    )

    assert geojson_preview_summary(geojson) == "1 feature(s); geometry: Polygon"

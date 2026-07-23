from pathlib import Path

import pytest

from ohqbuilder.ui.launcher import LauncherError, LauncherState, command_for_step


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

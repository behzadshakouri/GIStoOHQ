from pathlib import Path

import pytest

from ohqbuilder.ui.launcher import (
    LauncherError,
    LauncherState,
    clamp_zoom,
    command_for_step,
    map_click_to_lonlat,
    osm_tile_cache_path,
    geojson_preview_summary,
    load_project_config,
    save_project_config,
    state_from_config,
    update_config_from_state,
)


def test_osm_tile_cache_path_is_zoom_x_y_png(tmp_path):
    assert osm_tile_cache_path(14, 4688, 6260, cache_dir=tmp_path) == tmp_path / "14" / "4688" / "6260.png"


def test_clamp_zoom_keeps_osm_zoom_range():
    assert clamp_zoom(-10) == 1
    assert clamp_zoom(14) == 14
    assert clamp_zoom(25) == 19


def test_map_click_to_lonlat_returns_center_for_center_click():
    lon, lat = map_click_to_lonlat(-76.9765, 38.9921, 14, 384, 256)

    assert lon == pytest.approx(-76.9765)
    assert lat == pytest.approx(38.9921)


def test_map_click_to_lonlat_moves_east_and_north():
    center_lon = -76.9765
    center_lat = 38.9921

    east_lon, east_lat = map_click_to_lonlat(center_lon, center_lat, 14, 484, 256)
    north_lon, north_lat = map_click_to_lonlat(center_lon, center_lat, 14, 384, 156)

    assert east_lon > center_lon
    assert east_lat == pytest.approx(center_lat, abs=0.001)
    assert north_lat > center_lat
    assert north_lon == pytest.approx(center_lon)


def test_command_for_init_dem_config():
    command = command_for_step(
        "init-dem-config",
        LauncherState(
            config_path=Path("config.yaml"),
            site="SligoCreek",
            lon=-76.9765,
            lat=38.9921,
            flowline_path=Path("flowlines.geojson"),
            tile_index=Path("tiles.geojson"),
            target_crs="EPSG:26918",
            method="upstream_network",
        ),
    )

    assert command.argv == (
        "ohqbuild",
        "init-dem-config",
        "--config",
        "config.yaml",
        "--site",
        "SligoCreek",
        "--lon",
        "-76.9765",
        "--lat",
        "38.9921",
        "--flowlines",
        "flowlines.geojson",
        "--tile-index",
        "tiles.geojson",
        "--target-crs",
        "EPSG:26918",
        "--method",
        "upstream_network",
    )


def test_command_for_init_dem_config_keeps_config_relative_paths(tmp_path):
    config_path = tmp_path / "project" / "config.yaml"
    state = LauncherState(
        config_path=config_path,
        site="SligoCreek",
        lon=-76.9765,
        lat=38.9921,
        flowline_path=tmp_path / "project" / "hydro" / "flowlines.geojson",
        tile_index=tmp_path / "project" / "indexes" / "tiles.geojson",
    )

    command = command_for_step("init-dem-config", state)

    assert "hydro/flowlines.geojson" in command.argv
    assert "indexes/tiles.geojson" in command.argv


def test_command_for_init_dem_config_requires_outlet():
    with pytest.raises(LauncherError, match="outlet longitude"):
        command_for_step("init-dem-config", LauncherState(config_path=Path("config.yaml")))


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


def test_load_project_config_rejects_conflict_markers(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "dem_acquisition:\n"
        "<<<<<<< Updated upstream\n"
        "  method: upstream_network\n"
        "=======\n"
        "  method: polygon\n"
        ">>>>>>> branch\n",
        encoding="utf-8",
    )

    with pytest.raises(LauncherError, match="unresolved merge-conflict markers"):
        load_project_config(config)


def test_ui_config_load_state_update_and_save(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
site:
  name: SligoCreek
  target_crs: EPSG:26918
outlet:
  longitude: -76.9765
  latitude: 38.9921
dem_acquisition:
  method: upstream_network
  tile_manifest: intermediate/manifest.json
  flowline_path: hydro/flowlines.geojson
  tile_index: indexes/tiles.geojson
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
    assert state.raw_dem_dir == tmp_path / "dem" / "raw"
    assert state.lon == -76.9765
    assert state.lat == 38.9921
    assert state.method == "upstream_network"
    assert state.flowline_path == tmp_path / "hydro" / "flowlines.geojson"
    assert state.tile_index == tmp_path / "indexes" / "tiles.geojson"

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


def test_ui_launcher_builds_run_dem_prep_command(tmp_path):
    from ohqbuilder.ui.launcher import LauncherState, command_for_step

    state = LauncherState(config_path=tmp_path / "config.yaml")

    command = command_for_step("run-dem-prep", state)

    assert command.label == "Run DEM Prep"
    assert command.argv == ("ohqbuild", "run-dem-prep", "--config", str(tmp_path / "config.yaml"))


def test_ui_launcher_defaults_to_sligo_example_when_available():
    from ohqbuilder.ui.launcher import default_config_path

    assert default_config_path() == "examples/SligoCreek/dem_workflow.example.yaml"


def test_run_dem_ui_shell_wrapper_exists():
    script = Path("scripts/run_dem_ui.sh")

    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "ohqbuild ui" in text
    assert "python -m ohqbuilder.cli ui" in text

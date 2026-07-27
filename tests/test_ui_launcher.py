import json
from pathlib import Path

import pytest

from ohqbuilder.ui.launcher import (
    LauncherError,
    LauncherState,
    clamp_zoom,
    command_for_step,
    map_click_to_lonlat,
    nearest_point_on_lines,
    osm_tile_cache_path,
    rectangle_from_corners,
    geojson_preview_summary,
    load_project_config,
    save_project_config,
    sligo_demo_reset_args,
    snapped_outlet,
    state_from_config,
    state_with_config_defaults,
    update_config_from_state,
    use_expanded_acquisition,
    write_drawn_acquisition,
)


def test_osm_tile_cache_path_is_zoom_x_y_png(tmp_path):
    assert (
        osm_tile_cache_path(14, 4688, 6260, cache_dir=tmp_path)
        == tmp_path / "14" / "4688" / "6260.png"
    )


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


def test_nearest_point_on_lines_snaps_map_pick_to_demo_flowline():
    lines = [[[-76.9765, 38.9921], [-76.9850, 39.0200]]]

    lon, lat = nearest_point_on_lines(-76.9712, 38.9749, lines)

    assert lon == pytest.approx(-76.9765)
    assert lat == pytest.approx(38.9921)


def test_sligo_demo_reset_args_preserve_map_picked_coordinates(tmp_path):
    config_path = tmp_path / "examples" / "SligoCreek" / "dem_workflow.example.yaml"

    args = sligo_demo_reset_args(config_path, -76.99778601, 38.96888097)

    assert args["output_path"] == config_path
    assert args["site"] == "SligoCreekDemo"
    assert args["lon"] == -76.99778601
    assert args["lat"] == 38.96888097
    assert str(args["flowline_path"]) == "hydro/NHDFlowline.demo.geojson"
    assert str(args["tile_index"]) == "indexes/usgs_3dep_tiles.demo.geojson"


def test_state_with_config_defaults_keeps_map_picked_outlet_and_config_paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    config = {
        "site": {"name": "SligoCreekDemo", "target_crs": "EPSG:26918"},
        "outlet": {"longitude": -76.9765, "latitude": 38.9921},
        "dem_acquisition": {
            "method": "upstream_network",
            "flowline_path": "hydro/NHDFlowline.demo.geojson",
            "tile_index": "indexes/usgs_3dep_tiles.demo.geojson",
            "tile_manifest": "intermediate/dem_download_manifest.json",
        },
    }
    form = LauncherState(
        config_path=config_path,
        site=".",
        lon=-76.99778601,
        lat=38.96888097,
        method="upstream_network",
    )

    merged = state_with_config_defaults(form, config)

    assert merged.site == "SligoCreekDemo"
    assert merged.lon == -76.99778601
    assert merged.lat == 38.96888097
    assert merged.flowline_path == tmp_path / "hydro" / "NHDFlowline.demo.geojson"
    assert merged.tile_index == tmp_path / "indexes" / "usgs_3dep_tiles.demo.geojson"


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


def test_command_for_init_dem_config_snaps_outlet_to_flowline(tmp_path):
    flowlines = tmp_path / "flowlines.geojson"
    flowlines.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
        '"geometry":{"type":"LineString","coordinates":[[-76.9765,38.9921],[-76.985,39.02]]}}]}',
        encoding="utf-8",
    )
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        site="SligoCreek",
        lon=-76.98216483,
        lat=38.9882974,
        flowline_path=flowlines,
        method="upstream_network",
    )

    assert snapped_outlet(state) == pytest.approx((-76.9765, 38.9921))
    command = command_for_step("init-dem-config", state)

    assert command.argv[command.argv.index("--lon") + 1] == "-76.9765"
    assert command.argv[command.argv.index("--lat") + 1] == "38.9921"


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


def test_update_config_keeps_project_paths_relative_without_duplicate_prefix(tmp_path):
    config_path = tmp_path / "examples" / "SligoCreek" / "config.yaml"
    state = LauncherState(
        config_path=config_path,
        root=config_path.parent,
        source_dir=config_path.parent / "source_downloads",
        manifest_path=config_path.parent / "intermediate" / "manifest.json",
        raw_dem_dir=config_path.parent / "dem" / "raw",
        flowline_path=config_path.parent / "hydro" / "flowlines.geojson",
        tile_index=config_path.parent / "indexes" / "tiles.geojson",
    )

    updated = update_config_from_state({}, state)

    assert updated["root"] == "."
    assert updated["download_dir"] == "source_downloads"
    assert updated["dem_acquisition"]["flowline_path"] == "hydro/flowlines.geojson"
    assert updated["dem_acquisition"]["tile_index"] == "indexes/tiles.geojson"
    assert updated["dem_acquisition"]["tile_manifest"] == "intermediate/manifest.json"
    assert updated["paths"]["raw_dem_dir"] == "dem/raw"


def test_state_from_config_repairs_repo_relative_project_prefix(monkeypatch, tmp_path):
    project = tmp_path / "examples" / "SligoCreek"
    flowline = project / "hydro" / "flowlines.geojson"
    flowline.parent.mkdir(parents=True)
    flowline.write_text("{}", encoding="utf-8")
    config_path = project / "config.yaml"
    monkeypatch.chdir(tmp_path)

    state = state_from_config(
        config_path,
        {"dem_acquisition": {"flowline_path": "examples/SligoCreek/hydro/flowlines.geojson"}},
    )

    assert state.flowline_path == flowline


def test_geojson_preview_summary(tmp_path):
    geojson = tmp_path / "area.geojson"
    geojson.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[]},"properties":{}}]}',
        encoding="utf-8",
    )

    assert geojson_preview_summary(geojson) == "1 feature(s); geometry: Polygon"


def test_write_drawn_acquisition_closes_user_polygon(tmp_path):
    output = write_drawn_acquisition(
        tmp_path / "area.geojson",
        [(-77.0, 39.0), (-76.9, 39.0), (-76.9, 39.1), (-77.0, 39.1)],
    )

    data = json.loads(output.read_text(encoding="utf-8"))
    ring = data["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert data["features"][0]["properties"]["source"] == "launcher_map"


def test_rectangle_from_two_opposite_clicks_has_four_distinct_corners():
    rectangle = rectangle_from_corners((-77.01, 38.93), (-76.98, 38.95))

    assert rectangle == [
        (-77.01, 38.93),
        (-76.98, 38.93),
        (-76.98, 38.95),
        (-77.01, 38.95),
    ]
    assert len(set(rectangle)) == 4


def test_john_mccormack_example_config_is_loadable():
    config = load_project_config("examples/JohnMcCormack3600/dem_workflow.example.yaml")

    assert config["project"]["title"] == "3600 John McCormack Rd NE Plan Set 20240709"
    assert config["site"]["target_crs"] == "EPSG:26918"
    assert config["dem_acquisition"]["method"] == "outlet_buffer"


def test_use_expanded_acquisition_promotes_validation_output(tmp_path):
    config_path = tmp_path / "config.yaml"
    expanded = tmp_path / "intermediate" / "expanded.geojson"
    expanded.parent.mkdir()
    expanded.write_text("{}", encoding="utf-8")
    config = {
        "dem_acquisition": {
            "method": "upstream_network",
            "acquisition_area": "intermediate/area.geojson",
            "expanded_acquisition_area": "intermediate/expanded.geojson",
        }
    }

    assert use_expanded_acquisition(config_path, config) == expanded
    assert config["dem_acquisition"]["method"] == "polygon"
    assert config["dem_acquisition"]["acquisition_area"] == "intermediate/expanded.geojson"


@pytest.mark.parametrize(
    ("step", "subcommand"),
    [
        ("prepare-hydrology", "prepare-hydrology"),
        ("prepare-inputs", "prepare-inputs"),
        ("check-inputs", "check-inputs"),
        ("build-ohq", "build"),
    ],
)
def test_ui_launcher_builds_final_ohq_commands(step, subcommand):
    state = LauncherState(config_path=Path("config.yaml"), root=Path("project"), site="Demo")

    command = command_for_step(step, state)

    assert command.argv == ("ohqbuild", subcommand, "--root", "project", "--site", "Demo")


def test_continue_to_ohq_prepares_hydrology_before_combined_run():
    state = LauncherState(config_path=Path("config.yaml"), root=Path("project"), site="Demo")

    command = command_for_step("run-to-ohq", state)

    assert command.argv == ("ohqbuild", "prepare-hydrology", "--root", "project", "--site", "Demo")
    assert command.followup_argv == (("ohqbuild", "run", "--root", "project", "--site", "Demo"),)


def test_full_run_command_downloads_and_builds_from_verified_outlet(tmp_path):
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        site="Demo",
        source_dir=tmp_path / "source_downloads",
        target_crs="EPSG:26918",
        lon=-76.99,
        lat=38.94,
        acquisition_area=tmp_path / "intermediate" / "area.geojson",
    )

    command = command_for_step("full-run", state)

    assert command.argv[:2] == ("ohqbuild", "full-run")
    assert command.argv[command.argv.index("--lon") + 1] == "-76.99"
    assert command.argv[command.argv.index("--lat") + 1] == "38.94"
    assert command.argv[command.argv.index("--target-crs") + 1] == "EPSG:26918"
    assert command.argv[command.argv.index("--download-dir") + 1] == str(
        tmp_path / "source_downloads"
    )
    assert command.argv[command.argv.index("--acquisition-area") + 1] == str(
        tmp_path / "intermediate" / "area.geojson"
    )


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

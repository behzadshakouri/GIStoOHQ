import json
import os
import queue
import sys
import time
from pathlib import Path

import pytest

from ohqbuilder.ui.launcher import (
    BASEMAP_PROVIDERS,
    CommandRunner,
    LauncherError,
    LauncherState,
    RunnerFinished,
    WorkflowCommand,
    clamp_zoom,
    basemap_tile_cache_path,
    basemap_tile_url,
    command_for_step,
    map_click_to_lonlat,
    nearest_point_on_lines,
    osm_tile_cache_path,
    qgis_command,
    qgis_layer_paths,
    rectangle_from_corners,
    recommended_workflow_step,
    geojson_preview_summary,
    load_project_config,
    save_project_config,
    sligo_demo_reset_args,
    snapped_outlet,
    state_from_config,
    state_with_config_defaults,
    update_config_from_state,
    use_expanded_acquisition,
    workflow_prerequisite_error,
    write_drawn_acquisition,
)


def test_qgis_layer_paths_collects_generated_dem_and_delineation_files(tmp_path):
    site = tmp_path / "SITE_A"
    dem = site / "demlr" / "cliped_utm.tif"
    reaches = site / "outputs" / "reaches.gpkg"
    sidecar = site / "outputs" / "outlet.dbf"
    for path in (dem, reaches, sidecar):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    paths = qgis_layer_paths(
        LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="SITE_A")
    )

    assert set(paths) == {dem.resolve(), reaches.resolve()}


def test_qgis_layer_paths_sorts_latest_to_oldest(tmp_path):
    site = tmp_path / "SITE_A"
    oldest = site / "outputs" / "oldest.gpkg"
    newest = site / "outputs" / "newest.gpkg"
    for path in (oldest, newest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    os.utime(oldest, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newest, ns=(2_000_000_000, 2_000_000_000))

    paths = qgis_layer_paths(
        LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="SITE_A")
    )

    assert paths == (newest.resolve(), oldest.resolve())


def test_qgis_command_passes_every_generated_layer_to_qgis(tmp_path):
    layer = tmp_path / "SITE_A" / "outputs" / "watershed_boundary.gpkg"
    layer.parent.mkdir(parents=True)
    layer.touch()
    state = LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="SITE_A")

    assert qgis_command(state, executable="/usr/bin/qgis") == (
        "/usr/bin/qgis",
        "--nologo",
        str(layer.resolve()),
    )


def test_qgis_command_supplies_oldest_first_so_newest_is_topmost(tmp_path):
    site = tmp_path / "SITE_A"
    oldest = site / "outputs" / "oldest.gpkg"
    newest = site / "outputs" / "newest.gpkg"
    for path in (oldest, newest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    os.utime(oldest, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newest, ns=(2_000_000_000, 2_000_000_000))
    state = LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="SITE_A")

    assert qgis_command(state, executable="/usr/bin/qgis") == (
        "/usr/bin/qgis",
        "--nologo",
        str(oldest.resolve()),
        str(newest.resolve()),
    )


def test_launcher_builds_documented_watershed_import_command(tmp_path):
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        site="Sligo",
        lon=-76.974,
        lat=38.957,
        reference_source="https://example.gov/FeatureServer/2",
        reference_name_field="BASIN",
        reference_name="Sligo Creek",
        reference_title="County watershed inventory",
        reference_organization="Example County",
        reference_url="https://example.gov/watersheds",
    )

    command = command_for_step("import-watershed-reference", state)

    assert command.label == "Import Documented Watershed"
    assert command.argv[:2] == ("ohqbuild", "import-watershed-reference")
    assert command.argv[command.argv.index("--name") + 1] == "Sligo Creek"
    assert command.argv[command.argv.index("--source-organization") + 1] == "Example County"


def test_launcher_full_run_passes_documented_watershed_config(tmp_path):
    source = tmp_path / "Estimated Sligo Creek.kmz"
    source.write_bytes(b"kmz")
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        site="Sligo",
        lon=-76.9744266065,
        lat=38.9571888036,
        method="upstream_network",
        acquisition_area=tmp_path / "intermediate" / "dem_acquisition_area.geojson",
        reference_source=str(source),
        reference_title="Estimated Sligo Creek review outline",
        reference_organization="Operator digitized Google Earth review",
        reference_license="review artifact",
        reference_allow_outlet_outside=True,
    )

    command = command_for_step("full-run", state)
    full_run = command.followup_argv[0]

    assert "--documented-watershed-source" in full_run
    assert full_run[full_run.index("--documented-watershed-source") + 1] == str(source)
    assert (
        full_run[full_run.index("--documented-watershed-title") + 1]
        == "Estimated Sligo Creek review outline"
    )
    assert (
        full_run[full_run.index("--documented-watershed-organization") + 1]
        == "Operator digitized Google Earth review"
    )
    assert "--documented-watershed-allow-outlet-outside" in full_run


def test_launcher_keeps_reference_fields_in_compact_dialog():
    source = Path("ohqbuilder/ui/launcher.py").read_text(encoding="utf-8")

    assert 'text="Documented watershed…"' not in source  # tuple label, not main-form row
    assert '("Documented watershed…", self.configure_documented_watershed)' in source
    assert 'dialog.title("Documented Watershed Reference")' in source
    assert 'self.log = tk.Text(frame, height=14' in source


def test_launcher_groups_example_configs_under_menu_button():
    source = Path("ohqbuilder/ui/launcher.py").read_text(encoding="utf-8")

    assert 'text="Examples ▾"' in source
    assert 'label="Sligo Creek"' in source
    assert 'label="John McCormack (JM)"' in source
    assert '"Open John McCormack example"' not in source


def test_command_runner_stop_terminates_active_process():
    messages = queue.Queue()
    runner = CommandRunner(
        WorkflowCommand("Slow command", (sys.executable, "-c", "import time; time.sleep(30)")),
        messages,
    )
    runner.start()
    deadline = time.monotonic() + 3
    while runner.process is None and time.monotonic() < deadline:
        time.sleep(0.01)

    runner.cancel()
    runner.join(timeout=3)

    assert not runner.is_alive()
    queued = list(messages.queue)
    finished = [item for item in queued if isinstance(item, RunnerFinished)]
    assert finished[-1].status == 130
    assert any("Slow command cancelled by user" in item for item in queued if isinstance(item, str))
    assert any(
        f"RUN {runner.run_id}: Slow command STARTED" in item
        for item in queued
        if isinstance(item, str)
    )


def test_launcher_exposes_clear_log_control():
    source = Path("ohqbuilder/ui/launcher.py").read_text(encoding="utf-8")

    assert 'text="Clear log"' in source
    assert 'def clear_log(self)' in source
    assert 'self.log.delete("1.0", "end")' in source


def test_launcher_silences_startup_reload_and_unbuffers_workflow_output():
    source = Path("ohqbuilder/ui/launcher.py").read_text(encoding="utf-8")

    assert "self.load_config(announce=False)" in source
    assert "def load_config(self, announce: bool = True)" in source
    assert '"PYTHONUNBUFFERED": "1"' in source


def test_command_runner_propagates_run_id_and_unbuffered_python():
    messages = queue.Queue()
    command = WorkflowCommand(
        "Environment check",
        (
            sys.executable,
            "-c",
            "import os; print(os.environ['OHQ_RUN_ID'], os.environ['PYTHONUNBUFFERED'])",
        ),
    )
    runner = CommandRunner(command, messages)
    runner.start()
    runner.join(timeout=3)

    assert not runner.is_alive()
    output = "".join(item for item in list(messages.queue) if isinstance(item, str))
    assert f"{runner.run_id} 1" in output


def test_osm_tile_cache_path_is_zoom_x_y_png(tmp_path):
    assert (
        osm_tile_cache_path(14, 4688, 6260, cache_dir=tmp_path)
        == tmp_path / "14" / "4688" / "6260.png"
    )


def test_basemap_providers_offer_road_satellite_and_topographic_tiles(tmp_path):
    satellite = BASEMAP_PROVIDERS["Satellite"]
    topographic = BASEMAP_PROVIDERS["Topographic"]

    assert basemap_tile_url(satellite, 14, 4688, 6260).endswith("/14/6260/4688")
    assert basemap_tile_url(topographic, 14, 4688, 6260).endswith("/14/4688/6260.png")
    assert basemap_tile_cache_path(satellite, 14, 4688, 6260, cache_dir=tmp_path) == (
        tmp_path / "esri_world_imagery" / "14" / "4688" / "6260.png"
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


def test_bundled_sligo_config_uses_reviewed_routed_outlet():
    path = Path("examples/SligoCreek/dem_workflow.example.yaml")
    state = state_from_config(path, load_project_config(path))

    assert state.lon == pytest.approx(-76.9744266065)
    assert state.lat == pytest.approx(38.9571888036)
    assert state.reference_source == str(
        Path("examples/SligoCreek/Estimated Sligo Creek.kmz").resolve()
    )
    assert state.reference_allow_outlet_outside is True


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
    config_path.write_text("dem_acquisition: {}\n", encoding="utf-8")
    expanded = tmp_path / "intermediate" / "expanded.geojson"
    expanded.parent.mkdir()
    expanded.write_text("{}", encoding="utf-8")
    config = {
        "dem_acquisition": {
            "method": "upstream_network",
            "acquisition_area": "intermediate/area.geojson",
            "expanded_acquisition_area": "intermediate/expanded.geojson",
            "validation_summary": "intermediate/validation.json",
        }
    }
    (expanded.parent / "validation.json").write_text(
        json.dumps({"expanded_acquisition_area": "intermediate/expanded.geojson"}),
        encoding="utf-8",
    )

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


def test_init_dem_config_force_is_only_added_after_confirmation_state(tmp_path):
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        site="Demo",
        lon=-77.0,
        lat=39.0,
        method="outlet_buffer",
        overwrite_config=True,
    )

    assert "--force" in command_for_step("init-dem-config", state).argv


def test_continue_to_ohq_prepares_hydrology_before_combined_run():
    state = LauncherState(config_path=Path("config.yaml"), root=Path("project"), site="Demo")

    command = command_for_step("run-to-ohq", state)

    assert command.argv == ("ohqbuild", "prepare-hydrology", "--root", "project", "--site", "Demo")
    assert command.followup_argv == (("ohqbuild", "run", "--root", "project", "--site", "Demo"),)


def test_full_run_command_downloads_and_builds_from_verified_outlet(tmp_path):
    acquisition = tmp_path / "intermediate" / "area.geojson"
    acquisition.parent.mkdir()
    acquisition.write_text("{}", encoding="utf-8")
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        site="Demo",
        source_dir=tmp_path / "source_downloads",
        target_crs="EPSG:26918",
        lon=-76.99,
        lat=38.94,
        method="outlet_buffer",
        acquisition_area=acquisition,
        use_reviewed_pour_points=True,
        nhdplus_snap_distance_m=50.0,
        use_existing_outlet=True,
        reuse_downloads=True,
    )

    command = command_for_step("full-run", state)

    assert command.argv == ("ohqbuild", "prepare-dem", "--config", str(tmp_path / "config.yaml"))
    full_run = command.followup_argv[0]
    assert full_run[:2] == ("ohqbuild", "full-run")
    assert full_run[full_run.index("--config") + 1] == str(tmp_path / "config.yaml")
    assert full_run[full_run.index("--lon") + 1] == "-76.99"
    assert full_run[full_run.index("--lat") + 1] == "38.94"
    assert full_run[full_run.index("--target-crs") + 1] == "EPSG:26918"
    assert full_run[full_run.index("--download-dir") + 1] == str(
        tmp_path / "source_downloads"
    )
    assert full_run[full_run.index("--acquisition-area") + 1] == str(
        tmp_path / "intermediate" / "area.geojson"
    )
    assert "--use-reviewed-pour-points" in full_run
    assert full_run[full_run.index("--nhdplus-snap-distance-m") + 1] == "50.0"
    assert "--use-existing-outlet" in full_run
    assert "--reuse-downloads" in full_run


def test_launcher_promotes_reviewed_pour_points(tmp_path):
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        site="Demo",
        overwrite_promoted_pour_points=True,
    )

    command = command_for_step("promote-pour-points", state)

    assert command.argv == (
        "ohqbuild", "promote-pour-points", "--root", str(tmp_path),
        "--site", "Demo", "--overwrite",
    )


def test_full_run_generates_configured_area_when_file_does_not_exist(tmp_path):
    state = LauncherState(
        config_path=tmp_path / "config.yaml",
        root=tmp_path,
        site="Demo",
        lon=-76.99,
        lat=38.94,
        method="outlet_buffer",
        acquisition_area=tmp_path / "intermediate" / "area.geojson",
    )

    command = command_for_step("full-run", state)

    assert command.argv[:2] == ("ohqbuild", "prepare-dem")
    assert "--acquisition-area" in command.followup_argv[0]


def test_hms_buttons_build_and_validate_native_project(tmp_path):
    state = LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="Demo")

    build = command_for_step("build-hms", state)
    validate = command_for_step("validate-hms", state)

    assert build.argv == (
        "ohqbuild",
        "build-hms",
        "--root",
        str(tmp_path),
        "--site",
        "Demo",
        "--project-name",
        "Demo",
    )
    assert validate.argv == (
        "ohqbuild",
        "validate-hms",
        "--project",
        str(tmp_path / "Demo" / "outputs" / "hec_hms" / "Demo.hms"),
    )


def test_ui_prerequisites_direct_new_project_to_full_run(tmp_path):
    state = LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="NewSite")

    assert "FULL RUN" in workflow_prerequisite_error("download-dem-manifest", state)
    assert "materialize-inputs" in workflow_prerequisite_error("prepare-hydrology", state)
    assert "Prepare hydrology" in workflow_prerequisite_error("prepare-inputs", state)
    assert "Prepare GIS inputs" in workflow_prerequisite_error("build-hms", state)
    assert recommended_workflow_step(state) == "full-run"


def test_ui_prerequisites_block_promotion_without_candidates(tmp_path):
    state = LauncherState(config_path=tmp_path / "config.yaml", root=tmp_path, site="Demo")

    message = workflow_prerequisite_error("promote-pour-points", state)

    assert "No pour-point candidate file exists" in message


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
    assert '.venv/bin/ohqbuild" ui' in text
    assert "ohqbuild ui" in text
    assert 'python_command="${PYTHON:-python3}"' in text
    assert '-m ohqbuilder.cli ui "$@"' in text

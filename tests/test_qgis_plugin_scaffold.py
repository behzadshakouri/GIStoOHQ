from pathlib import Path

import yaml


def test_qgis_plugin_scaffold_files_exist():
    root = Path("qgis_plugin/gistoohq_dem_workflow")

    assert (root / "metadata.txt").is_file()
    assert (root / "__init__.py").is_file()
    assert (root / "plugin.py").is_file()
    assert (root / "dock.py").is_file()
    assert "GIStoOHQ DEM Workflow" in (root / "metadata.txt").read_text(encoding="utf-8")


def test_qgis_plugin_update_script_refreshes_link_and_bytecode():
    script = Path("scripts/update_qgis_plugin.sh")
    source = script.read_text(encoding="utf-8")

    assert script.is_file()
    assert "install_qgis_plugin.sh" in source
    assert "__pycache__" in source
    assert "'*.pyc'" in source
    assert "disable and re-enable the plugin" in source


def test_qgis_plugin_registers_an_actual_qdockwidget():
    plugin = Path("qgis_plugin/gistoohq_dem_workflow/plugin.py").read_text(encoding="utf-8")

    assert "self.dock_content = DemWorkflowDock(self.iface)" in plugin
    assert "self.dock = self.dock_content.widget" in plugin
    assert "addDockWidget(Qt.RightDockWidgetArea, self.dock)" in plugin
    assert "addDockWidget(Qt.RightDockWidgetArea, self.dock_content)" not in plugin


def test_qgis_plugin_dock_has_outlet_capture_hook():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Pick Outlet on Map" in dock
    assert "Set Outlet Coordinates" in dock
    assert "QgsMapToolEmitPoint" in dock
    assert "write_outlet" in dock


def test_qgis_plugin_dock_has_pour_point_capture_and_coordinate_entry():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Pick Pour Points on Map" in dock
    assert "Add Pour Point Coordinates" in dock
    assert "PourPointCaptureTool" in dock
    assert "manual_subwatershed_outlet" in dock
    assert 'feature["review_status"] = "pending"' in dock
    assert "finish_pour_point_capture" in dock
    assert "No pour-point candidate file exists" in dock
    assert "_refresh_action_buttons" in dock


def test_qgis_plugin_dock_can_use_canvas_extent_as_area():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Use Canvas Extent as DEM Area" in dock
    assert "qgis_canvas_extent" in dock
    assert "use_canvas_extent_as_area" in dock


def test_qgis_plugin_dock_has_draw_polygon_tool():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Draw DEM Area Polygon" in dock
    assert "AcquisitionPolygonTool" in dock
    assert "qgis_drawn_polygon" in dock


def test_qgis_plugin_dock_exposes_documented_watershed_import():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Configure Documented Watershed" in dock
    assert "Import Documented Watershed" in dock
    assert "documented_watershed" in dock
    assert "DocumentedWatershed_reference.gpkg" in dock
    assert "QTabWidget" in dock
    assert 'tabs.addTab(map_tab, "Map")' in dock
    assert 'dialog.setMinimumWidth(620)' in dock


def test_qgis_plugin_dock_loads_tile_preview_layers():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "tile_index" in dock
    assert "selected_tile_footprints" in dock
    assert "_write_manifest_footprints" in dock


def test_qgis_plugin_builds_command_specific_args(tmp_path):
    from qgis_plugin.gistoohq_dem_workflow.dock import _command_for_workflow

    config = tmp_path / "project.json"
    config.write_text(
        """
{
  "root": "project-root",
  "site": {"name": "SligoCreek", "target_crs": "EPSG:26918"},
  "outlet": {"longitude": -76.97, "latitude": 38.99},
  "download_dir": "downloads",
  "documented_watershed": {
    "source": "https://example.gov/FeatureServer/2",
    "name_field": "BASIN",
    "name": "Sligo Creek",
    "title": "County watershed inventory",
    "organization": "Example County"
  },
  "use_reviewed_pour_points": true,
  "nhdplus_snap_distance_m": 50,
  "dem_acquisition": {
    "acquisition_area": "intermediate/area.geojson",
    "tile_manifest": "intermediate/dem_download_manifest.json",
    "raw_dem_dir": "dem/raw"
  }
}
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "intermediate").mkdir()
    (tmp_path / "intermediate" / "area.geojson").write_text("{}", encoding="utf-8")

    assert _command_for_workflow("prepare-dem", str(config)) == [
        "ohqbuild",
        "prepare-dem",
        "--config",
        str(config),
    ]
    reference_command = _command_for_workflow("import-watershed-reference", str(config))
    assert reference_command[:2] == ["ohqbuild", "import-watershed-reference"]
    assert reference_command[reference_command.index("--name") + 1] == "Sligo Creek"
    assert reference_command[reference_command.index("--source-organization") + 1] == (
        "Example County"
    )
    assert _command_for_workflow("download-dem-manifest", str(config)) == [
        "ohqbuild",
        "download-dem-manifest",
        "--manifest",
        str(tmp_path / "intermediate/dem_download_manifest.json"),
        "--out-dir",
        str(tmp_path / "dem/raw"),
    ]
    assert _command_for_workflow("materialize-inputs", str(config)) == [
        "ohqbuild",
        "materialize-inputs",
        "--root",
        str(tmp_path / "project-root"),
        "--site",
        "SligoCreek",
        "--source-dir",
        str(tmp_path / "downloads"),
        "--target-crs",
        "EPSG:26918",
        "--dem-manifest",
        str(tmp_path / "intermediate/dem_download_manifest.json"),
    ]
    full_run = _command_for_workflow("full-run", str(config))
    assert full_run[:2] == ["ohqbuild", "full-run"]
    assert full_run[full_run.index("--lon") + 1] == "-76.97"
    assert full_run[full_run.index("--lat") + 1] == "38.99"
    assert "--target-crs" in full_run
    assert "--download-dir" in full_run
    assert "--use-reviewed-pour-points" in full_run
    assert full_run[full_run.index("--nhdplus-snap-distance-m") + 1] == "50"
    assert full_run[full_run.index("--minimum-watershed-area-km2") + 1] == "0.05"
    assert full_run[full_run.index("--minimum-subwatershed-area-km2") + 1] == "0.0005"
    assert full_run[full_run.index("--minimum-area-ratio") + 1] == "0.75"
    assert full_run[full_run.index("--maximum-area-ratio") + 1] == "1.25"
    assert _command_for_workflow("create-pour-points", str(config))[-1] == "--overwrite"
    assert full_run[full_run.index("--acquisition-area") + 1] == str(
        tmp_path / "intermediate/area.geojson"
    )
    assert _command_for_workflow("promote-pour-points", str(config)) == [
        "ohqbuild", "promote-pour-points", "--root", str(tmp_path / "project-root"),
        "--site", "SligoCreek",
    ]
    overridden = _command_for_workflow(
        "full-run",
        str(config),
        use_reviewed_pour_points=False,
        nhdplus_snap_distance_m=25.0,
        use_existing_outlet=True,
        reuse_downloads=True,
    )
    assert "--use-reviewed-pour-points" not in overridden
    assert overridden[overridden.index("--nhdplus-snap-distance-m") + 1] == "25.0"
    assert "--use-existing-outlet" in overridden
    assert "--reuse-downloads" in overridden
    assert _command_for_workflow(
        "promote-pour-points", str(config), overwrite_promoted_pour_points=True
    )[-1] == "--overwrite"


def test_qgis_plugin_download_command_requires_manifest(tmp_path):
    from qgis_plugin.gistoohq_dem_workflow.dock import QgisDockConfigError, _command_for_workflow

    config = tmp_path / "project.json"
    config.write_text('{"dem_acquisition": {}}\n', encoding="utf-8")

    try:
        _command_for_workflow("download-dem-manifest", str(config))
    except QgisDockConfigError as exc:
        assert "tile_manifest" in str(exc)
    else:
        raise AssertionError("Expected QgisDockConfigError")


def test_qgis_plugin_runs_commands_with_qprocess():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "QProcess" in dock
    assert "readyReadStandardOutput" in dock
    assert "readyReadStandardError" in dock
    assert "A workflow command is already running" in dock


def test_qgis_plugin_loads_raw_and_snapped_outlet_layers():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "outlet_raw" in dock
    assert "outlet_snapped" in dock
    assert "raw_path" in dock
    assert "snapped_path" in dock


def test_qgis_plugin_has_direct_dem_prep_button():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Run Direct DEM Prep" in dock
    assert "run-dem-prep" in dock


def test_qgis_plugin_and_tk_launcher_build_identical_core_commands(tmp_path):
    from ohqbuilder.ui.launcher import command_for_step, state_from_config
    from qgis_plugin.gistoohq_dem_workflow.dock import _command_for_workflow

    config = {
        "root": "project-root",
        "site": {"name": "Demo", "target_crs": "EPSG:26918"},
        "outlet": {"longitude": -76.97, "latitude": 38.99},
        "dem_acquisition": {
            "method": "polygon",
            "acquisition_area": "intermediate/area.geojson",
            "tile_manifest": "intermediate/manifest.json",
        },
        "documented_watershed": {
            "source": "references/watershed.gpkg",
            "title": "Reviewed watershed",
            "organization": "Example agency",
            "layer": "boundary",
            "name_field": "NAME",
            "name": "Demo watershed",
            "url": "https://example.test/watershed",
            "license": "Public domain",
        },
    }
    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "intermediate").mkdir()
    (tmp_path / "intermediate" / "area.geojson").write_text("{}", encoding="utf-8")
    state = state_from_config(config_path, config)

    commands = (
        ("prepare-dem", "prepare-dem"),
        ("run-dem-prep", "run-dem-prep"),
        ("download-dem-manifest", "download-dem-manifest"),
        ("materialize-inputs", "materialize-inputs"),
        ("validate-dem", "validate-dem"),
        ("prepare-hydrology", "prepare-hydrology"),
        ("prepare-inputs", "prepare-inputs"),
        ("check-inputs", "check-inputs"),
        ("build-ohq", "build"),
        ("full-run", "full-run"),
        ("promote-pour-points", "promote-pour-points"),
        ("import-watershed-reference", "import-watershed-reference"),
        ("build-hms", "build-hms"),
        ("validate-hms", "validate-hms"),
    )
    for launcher_step, plugin_command in commands:
        assert command_for_step(launcher_step, state).argv == tuple(
            _command_for_workflow(plugin_command, str(config_path))
        )


def test_qgis_plugin_exposes_full_download_to_ohq_workflow():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "FULL RUN: Download All Data to OHQ" in dock
    assert 'command == "full-run"' in dock
    assert "prepare-hydrology" in dock
    assert "Build HEC-HMS" in dock
    assert "Validate HEC-HMS" in dock


def test_qgis_plugin_has_optional_watershed_data_tab_and_commands():
    from qgis_plugin.gistoohq_dem_workflow.dock import _command_for_watershed_data

    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")
    assert 'tabs.insertTab(2, data_tab, "Data")' in dock
    assert "Open Watershed Data…" in dock
    assert "Download Declared Product" in dock
    assert "QScrollArea" in dock
    assert "buttons.addWidget(button, index // 3, index % 3)" in dock

    command = _command_for_watershed_data(
        "acquire-url", site_spec="site.yaml", url="https://example.gov/weather.csv",
        provider="example", product="hourly-weather", product_version="2026",
        cache="cache", catalog="package/catalog.json",
    )
    assert command == [
        "ohqbuild", "data", "acquire-url", "--url", "https://example.gov/weather.csv",
        "--provider", "example", "--product", "hourly-weather",
        "--product-version", "2026", "--cache", "cache",
        "--catalog", "package/catalog.json",
    ]
    assert _command_for_watershed_data(
        "reconnaissance", site_spec="site.yaml", reconnaissance_output="recon", radius_km=25,
    ) == [
        "ohqbuild", "data", "reconnaissance", "--site-spec", "site.yaml",
        "--output", "recon", "--radius-km", "25",
    ]
    assert _command_for_watershed_data(
        "download-discharge", site_spec="site.yaml", station_id="01649500",
        cache="cache", catalog="package/catalog.json",
    ) == [
        "ohqbuild", "data", "download-discharge", "--site-spec", "site.yaml",
        "--station-id", "01649500", "--cache", "cache",
        "--catalog", "package/catalog.json",
    ]
    assert _command_for_watershed_data(
        "download-weather", site_spec="site.yaml", cache="cache",
        catalog="package/catalog.json", weather_variables="PRECTOTCORR,T2M",
    )[-2:] == ["--variables", "PRECTOTCORR,T2M"]
    assert _command_for_watershed_data(
        "run", site_spec="site.yaml", station_id="01649500", workspace="run",
    )[-3:] == ["--workspace", "run", "--export-hydropinn"]
    assert _command_for_watershed_data(
        "forecast-view", site_spec="site.yaml", asset_id="sha256:x",
        prediction_time="2025-01-01T00:00:00Z", cache="cache", catalog="catalog.json",
    )[2] == "forecast-view"
    assert _command_for_watershed_data(
        "status", site_spec="site.yaml", catalog="catalog.json", cache="cache",
        status_output="status",
    )[-2:] == ["--output", "status"]

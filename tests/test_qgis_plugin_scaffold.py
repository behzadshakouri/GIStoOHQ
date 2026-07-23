from pathlib import Path


def test_qgis_plugin_scaffold_files_exist():
    root = Path("qgis_plugin/gistoohq_dem_workflow")

    assert (root / "metadata.txt").is_file()
    assert (root / "__init__.py").is_file()
    assert (root / "plugin.py").is_file()
    assert (root / "dock.py").is_file()
    assert "GIStoOHQ DEM Workflow" in (root / "metadata.txt").read_text(encoding="utf-8")


def test_qgis_plugin_dock_has_outlet_capture_hook():
    dock = Path("qgis_plugin/gistoohq_dem_workflow/dock.py").read_text(encoding="utf-8")

    assert "Pick Outlet on Map" in dock
    assert "QgsMapToolEmitPoint" in dock
    assert "write_outlet" in dock


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
  "download_dir": "downloads",
  "dem_acquisition": {
    "tile_manifest": "intermediate/dem_download_manifest.json",
    "raw_dem_dir": "dem/raw"
  }
}
""".strip(),
        encoding="utf-8",
    )

    assert _command_for_workflow("prepare-dem", str(config)) == [
        "ohqbuild",
        "prepare-dem",
        "--config",
        str(config),
    ]
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

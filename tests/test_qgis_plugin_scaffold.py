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

from pathlib import Path


def test_qgis_plugin_scaffold_files_exist():
    root = Path("qgis_plugin/gistoohq_dem_workflow")

    assert (root / "metadata.txt").is_file()
    assert (root / "__init__.py").is_file()
    assert (root / "plugin.py").is_file()
    assert (root / "dock.py").is_file()
    assert "GIStoOHQ DEM Workflow" in (root / "metadata.txt").read_text(encoding="utf-8")

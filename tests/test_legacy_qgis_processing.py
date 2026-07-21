from pathlib import Path


LEGACY_SCRIPTS = [
    Path("scripts/legacy_gis/fillsink_etc.py"),
    Path("scripts/legacy_gis/delineatewatershed.py"),
]


def test_legacy_qgis_scripts_do_not_require_processing_core_import():
    for script in LEGACY_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert "from processing.core.Processing import Processing" not in source
        assert "def initialize_processing" in source
        assert "Grass7AlgorithmProvider" in source
        assert "processing.algs.grass7.Grass7AlgorithmProvider" in source


def test_phase1_support_module_is_packaged():
    source = Path("scripts/legacy_gis/ws3io.py").read_text(encoding="utf-8")
    assert "def release_and_delete" in source
    assert "QgsProject" in source


def test_legacy_grass_helpers_prefer_current_grass_prefix():
    for script in (
        Path("scripts/legacy_gis/delineate_whole_watershed.py"),
        Path("scripts/legacy_gis/extract_reaches.py"),
    ):
        source = script.read_text(encoding="utf-8")
        assert 'for prefix in ("grass:", "grass7:")' in source


def test_whole_watershed_has_python_water_outlet_fallback():
    source = Path("scripts/legacy_gis/delineate_whole_watershed.py").read_text(encoding="utf-8")
    assert "def delineate_watershed_with_flowdir" in source
    assert "GRASS r.water.outlet failed; using Python D8 fallback" in source

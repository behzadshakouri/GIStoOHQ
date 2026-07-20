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

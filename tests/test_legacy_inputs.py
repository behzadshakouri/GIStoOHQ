import sys
import types

import pytest

from ohqbuilder.legacy_inputs import LegacyInputWorkflowError, run_legacy_input_workflow


def test_prepare_inputs_requires_qgis_environment():
    sys.modules.pop("qgis", None)
    sys.modules.pop("qgis.core", None)

    with pytest.raises(LegacyInputWorkflowError, match="QGIS Python environment"):
        run_legacy_input_workflow("/tmp/root", "WS3_GIS/AZ12-100", phase="phase1")


def test_prepare_inputs_runs_selected_phase_with_seeded_namespace(tmp_path, monkeypatch):
    qgis = types.ModuleType("qgis")
    qgis_core = types.ModuleType("qgis.core")
    processing = types.ModuleType("processing")
    monkeypatch.setitem(sys.modules, "qgis", qgis)
    monkeypatch.setitem(sys.modules, "qgis.core", qgis_core)
    monkeypatch.setitem(sys.modules, "processing", processing)

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    marker = tmp_path / "phase1_marker.txt"
    (script_dir / "run_phase1.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(ROOT + '\\n' + SITE_DIR + '\\n' + SCRIPT_DIR)\n",
        encoding="utf-8",
    )

    run_legacy_input_workflow(tmp_path / "root", "SITE_A", script_dir, phase="phase1")

    assert marker.read_text(encoding="utf-8") == (
        str((tmp_path / "root").resolve()) + "\n" + "SITE_A" + "\n" + str(script_dir.resolve())
    )

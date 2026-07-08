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

    out_dir = tmp_path / "root" / "SITE_A" / "outputs"
    out_dir.mkdir(parents=True)
    (out_dir / "outlet.shp").write_text("", encoding="utf-8")
    (out_dir / "NHDFlowline_clip.gpkg").write_text("", encoding="utf-8")
    dem_dir = tmp_path / "root" / "SITE_A" / "demlr"
    dem_dir.mkdir()
    (dem_dir / "cliped_utm.tif").write_text("", encoding="utf-8")

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


def test_prepare_inputs_reports_missing_phase1_inputs(tmp_path, monkeypatch):
    import types

    monkeypatch.setitem(sys.modules, "qgis", types.ModuleType("qgis"))
    monkeypatch.setitem(sys.modules, "qgis.core", types.ModuleType("qgis.core"))
    monkeypatch.setitem(sys.modules, "processing", types.ModuleType("processing"))
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "run_phase1.py").write_text("", encoding="utf-8")

    try:
        run_legacy_input_workflow(tmp_path / "root", "SITE_A", script_dir, phase="phase1")
    except LegacyInputWorkflowError as exc:
        message = str(exc)
        assert "Missing required phase1 input" in message
        assert "cliped_utm.tif" in message
        assert "NHDFlowline_clip.gpkg" in message
    else:
        raise AssertionError("expected LegacyInputWorkflowError")


def test_write_input_manifest_creates_directories_and_checklist(tmp_path):
    from ohqbuilder.legacy_inputs import write_input_manifest

    manifest = write_input_manifest(tmp_path, "SITE_A")

    assert (tmp_path / "SITE_A" / "outputs").is_dir()
    assert (tmp_path / "SITE_A" / "demlr").is_dir()
    text = manifest.read_text(encoding="utf-8")
    assert "demlr/cliped_utm.tif" in text
    assert "outputs/NHDFlowline_clip.gpkg" in text

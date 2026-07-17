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
    for suffix in (".shp", ".shx", ".dbf"):
        (out_dir / f"outlet{suffix}").write_text("", encoding="utf-8")
    (out_dir / "NHDFlowline_clip.gpkg").write_text("", encoding="utf-8")
    (out_dir / "flow_dir.tif").write_text("", encoding="utf-8")
    (out_dir / "flow_acc.tif").write_text("", encoding="utf-8")
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


def test_prepare_inputs_passes_configurable_paths_to_phase_script(tmp_path, monkeypatch):
    qgis = types.ModuleType("qgis")
    qgis_core = types.ModuleType("qgis.core")
    processing = types.ModuleType("processing")
    monkeypatch.setitem(sys.modules, "qgis", qgis)
    monkeypatch.setitem(sys.modules, "qgis.core", qgis_core)
    monkeypatch.setitem(sys.modules, "processing", processing)

    from ohqbuilder.legacy_inputs import LegacyWorkflowOptions

    root = tmp_path / "root"
    out_dir = tmp_path / "custom_outputs"
    out_dir.mkdir(parents=True)
    for suffix in (".shp", ".shx", ".dbf"):
        (out_dir / f"custom_outlet{suffix}").write_text("", encoding="utf-8")
    dem_path = tmp_path / "custom_dem.tif"
    dem_path.write_text("", encoding="utf-8")
    flowline_path = tmp_path / "flowlines.gpkg"
    flowline_path.write_text("", encoding="utf-8")
    flowdir_path = tmp_path / "flow_dir.tif"
    flowdir_path.write_text("", encoding="utf-8")
    flowacc_path = tmp_path / "flow_acc.tif"
    flowacc_path.write_text("", encoding="utf-8")

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    marker = tmp_path / "namespace.txt"
    (script_dir / "run_phase1.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('\\n'.join([OUT_DIR, DEM_PATH, OUTLET_PATH, FLOWLINE_PATH, FLOWDIR_PATH, FLOWACC_PATH, str(TARGET_EPSG), str(FORCE), str(DRY_RUN)]))\n",
        encoding="utf-8",
    )

    run_legacy_input_workflow(
        root,
        "SITE_A",
        script_dir,
        phase="phase1",
        options=LegacyWorkflowOptions(
            out_dir=out_dir,
            dem_path=dem_path,
            outlet_path=out_dir / "custom_outlet.shp",
            flowline_path=flowline_path,
            flowdir_path=flowdir_path,
            flowacc_path=flowacc_path,
            target_epsg=26918,
            force=False,
            dry_run=True,
        ),
    )

    assert marker.read_text(encoding="utf-8").splitlines() == [
        str(out_dir.resolve()),
        str(dem_path.resolve()),
        str((out_dir / "custom_outlet.shp").resolve()),
        str(flowline_path.resolve()),
        str(flowdir_path.resolve()),
        str(flowacc_path.resolve()),
        "26918",
        "False",
        "True",
    ]


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
        assert "flow_dir.tif" in message
        assert "flow_acc.tif" in message
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
    assert "outputs/flow_dir.tif" in text
    assert "outputs/flow_acc.tif" in text


def test_phase2_automatically_generates_missing_pour_points(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "qgis", types.ModuleType("qgis"))
    monkeypatch.setitem(sys.modules, "qgis.core", types.ModuleType("qgis.core"))
    monkeypatch.setitem(sys.modules, "processing", types.ModuleType("processing"))

    outputs = tmp_path / "root" / "SITE_A" / "outputs"
    outputs.mkdir(parents=True)
    for name in ("watershed_boundary.gpkg", "reaches.gpkg", "junctions.gpkg"):
        (outputs / name).write_text("", encoding="utf-8")
    for suffix in (".shp", ".shx", ".dbf"):
        (outputs / f"outlet{suffix}").write_text("", encoding="utf-8")

    calls = []

    def fake_generate(junctions, output):
        calls.append((junctions, output))
        for suffix in (".shp", ".shx", ".dbf"):
            output.with_suffix(suffix).write_text("", encoding="utf-8")
        return types.SimpleNamespace(count=2, output_path=output)

    monkeypatch.setattr("ohqbuilder.legacy_inputs.generate_pour_points", fake_generate)
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    marker = tmp_path / "phase2-ran"
    (script_dir / "run_phase2.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8"
    )

    run_legacy_input_workflow(tmp_path / "root", "SITE_A", script_dir, phase="phase2")

    assert calls == [(outputs / "junctions.gpkg", outputs / "pour_points.shp")]
    assert marker.is_file()

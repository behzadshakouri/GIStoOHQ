import sys

from ohqbuilder.app_runner import PipelineConfig, build_steps, run_pipeline


def test_pipeline_config_requires_root_and_site():
    try:
        PipelineConfig.from_mapping({"root": "/tmp/root"})
    except ValueError as exc:
        assert "root" in str(exc)
        assert "site" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_steps_skips_prepare_when_required_outputs_exist(tmp_path):
    root = tmp_path / "root"
    outputs = root / "SITE_A" / "outputs"
    outputs.mkdir(parents=True)
    for name in ("topology.gpkg", "subwatershed_params.gpkg", "reaches.gpkg", "junctions.gpkg"):
        (outputs / name).write_text("placeholder", encoding="utf-8")

    config = PipelineConfig.from_mapping({"root": str(root), "site": "SITE_A"})

    steps = build_steps(config)

    prepare = next(step for step in steps if step.name == "prepare-inputs")
    assert prepare.skipped
    assert prepare.reason == "required outputs already exist"


def test_build_steps_respects_force_and_options(tmp_path):
    config = PipelineConfig.from_mapping({
        "root": str(tmp_path),
        "site": "SITE_A",
        "script_dir": "/scripts",
        "phase": "phase2",
        "force": True,
        "no_schema": True,
        "strict_gis": True,
        "project_name": "Custom",
        "out": "/tmp/out.ohq",
    })

    steps = build_steps(config)
    by_name = {step.name: step for step in steps}

    assert not by_name["prepare-inputs"].skipped
    assert by_name["doctor"].command[-1] == "--strict-gis"
    assert by_name["prepare-inputs"].command[-2:] == ["--phase", "phase2"]
    assert "--no-schema" in by_name["check-inputs"].command
    assert ["--project-name", "Custom"] == by_name["build"].command[-5:-3]
    assert by_name["build"].command[-3:] == ["--out", "/tmp/out.ohq", "--no-schema"]


def test_run_pipeline_stops_on_failed_step(tmp_path, monkeypatch):
    config = PipelineConfig.from_mapping({"root": str(tmp_path), "site": "SITE_A", "skip_prepare": True})
    calls = []

    def fake_run(command, cwd=None):
        calls.append(command)
        return type("Completed", (), {"returncode": 7 if "check-inputs" in command else 0})()

    monkeypatch.setattr("ohqbuilder.app_runner.subprocess.run", fake_run)

    result = run_pipeline(config)

    assert result.returncode == 7
    assert result.failed_step == "check-inputs"
    assert result.completed == ["doctor"]
    assert result.skipped == ["prepare-inputs"]
    assert calls[0][:3] == [sys.executable, "-m", "ohqbuilder.cli"]


def test_run_pipeline_dry_run_does_not_call_subprocess(tmp_path, monkeypatch):
    config = PipelineConfig.from_mapping({"root": str(tmp_path), "site": "SITE_A", "skip_prepare": True})

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess should not be called")

    monkeypatch.setattr("ohqbuilder.app_runner.subprocess.run", fail_run)

    result = run_pipeline(config, dry_run=True)

    assert result.returncode == 0
    assert result.completed == ["doctor", "check-inputs", "build"]
    assert result.skipped == ["prepare-inputs"]


def test_run_py_reexecs_python3_on_old_python(monkeypatch):
    import os
    import runpy
    import shutil
    import sys

    calls = []

    def fake_execv(executable, argv):
        calls.append((executable, argv))
        raise SystemExit(0)

    monkeypatch.setattr(sys, "version_info", (3, 8))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3.11" if name == "python3.11" else None)
    monkeypatch.setattr(os, "execv", fake_execv)

    try:
        runpy.run_path("run.py", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit")

    assert calls
    assert calls[0][0] == "/usr/bin/python3.11"


def test_run_py_exits_before_import_when_no_python3_found(monkeypatch):
    import runpy
    import shutil
    import sys

    monkeypatch.setattr(sys, "version_info", (3, 8))
    monkeypatch.setattr(shutil, "which", lambda name: None)

    try:
        runpy.run_path("run.py", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected SystemExit")


def test_app_runner_creates_default_config_from_example(tmp_path, monkeypatch):
    from ohqbuilder import app_runner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.json").write_text(
        '{"root": "/tmp/root", "site": "SITE_A"}',
        encoding="utf-8",
    )

    status = app_runner.main(["config.json"])

    assert status == 2
    created = __import__("json").loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert created["root"] == str(tmp_path)
    assert created["site"] == "."


def test_app_runner_missing_non_default_config_returns_error(tmp_path, monkeypatch, capsys):
    from ohqbuilder import app_runner

    monkeypatch.chdir(tmp_path)

    status = app_runner.main(["missing.json"])

    assert status == 2
    assert "Pipeline config not found" in capsys.readouterr().err


def test_pipeline_config_invalid_json_is_actionable(tmp_path):
    from ohqbuilder.app_runner import PipelineConfig, PipelineConfigError

    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")

    try:
        PipelineConfig.from_file(bad)
    except PipelineConfigError as exc:
        assert "Invalid JSON" in str(exc)
    else:
        raise AssertionError("expected PipelineConfigError")


def test_pipeline_config_placeholder_root_defaults_to_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = PipelineConfig.from_mapping({"root": "/path/to/NHA", "site": "WS3_GIS/AZ12-100"})

    assert config.root == tmp_path.resolve()
    assert config.site == "."


def test_build_steps_makes_doctor_strict_when_prepare_will_run(tmp_path):
    config = PipelineConfig.from_mapping({"root": str(tmp_path), "site": "SITE_A"})

    steps = build_steps(config)

    doctor = next(step for step in steps if step.name == "doctor")
    assert "--strict-gis" in doctor.command

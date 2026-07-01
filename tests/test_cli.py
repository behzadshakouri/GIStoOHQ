from ohqbuilder.cli import build_parser, main
from ohqbuilder.legacy_inputs import LegacyInputWorkflowError


def test_prepare_inputs_parser_defaults_to_all_phases():
    args = build_parser().parse_args([
        "prepare-inputs",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
    ])

    assert args.command == "prepare-inputs"
    assert args.phase == "all"
    assert args.script_dir is None


def test_prepare_inputs_cli_returns_error_when_legacy_workflow_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise LegacyInputWorkflowError("no qgis")

    monkeypatch.setattr("ohqbuilder.cli.run_legacy_input_workflow", fail)

    assert main(["prepare-inputs", "--root", "/tmp/root", "--site", "SITE_A"]) == 2


def test_check_inputs_parser_supports_no_schema():
    args = build_parser().parse_args([
        "check-inputs",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
        "--no-schema",
    ])

    assert args.command == "check-inputs"
    assert args.no_schema is True


def test_run_parser_supports_end_to_end_options():
    args = build_parser().parse_args([
        "run",
        "--root",
        "/tmp/root",
        "--site",
        "SITE_A",
        "--project-name",
        "Custom",
        "--out",
        "/tmp/out.ohq",
        "--skip-prepare",
        "--no-schema",
    ])

    assert args.command == "run"
    assert args.project_name == "Custom"
    assert args.skip_prepare is True
    assert args.no_schema is True


def test_run_command_can_skip_prepare_and_build(monkeypatch, tmp_path):
    calls = []

    class OkValidator:
        def validate(self, settings, check_schema=True):
            calls.append(("validate", settings.project_name, check_schema))
            return type("Result", (), {"warnings": [], "errors": [], "ok": True})()

    def fake_build(settings, output_path=None, dry_run=False):
        calls.append(("build", settings.project_name, output_path, dry_run))
        return str(output_path or "default.ohq")

    monkeypatch.setattr("ohqbuilder.cli.InputValidator", OkValidator)
    monkeypatch.setattr("ohqbuilder.cli.build_ohq_project", fake_build)

    out = tmp_path / "out.ohq"
    status = main([
        "run",
        "--root",
        str(tmp_path),
        "--site",
        "SITE_A",
        "--project-name",
        "Custom",
        "--out",
        str(out),
        "--skip-prepare",
    ])

    assert status == 0
    assert calls == [
        ("validate", "Custom", True),
        ("build", "Custom", out.resolve(), False),
    ]


def test_run_command_stops_when_prepare_fails(monkeypatch):
    def fail(*args, **kwargs):
        raise LegacyInputWorkflowError("no qgis")

    monkeypatch.setattr("ohqbuilder.cli.run_legacy_input_workflow", fail)

    status = main(["run", "--root", "/tmp/root", "--site", "SITE_A"])

    assert status == 2

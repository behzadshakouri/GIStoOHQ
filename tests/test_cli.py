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

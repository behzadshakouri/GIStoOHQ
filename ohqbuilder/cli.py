from __future__ import annotations

import argparse
import json
from pathlib import Path

from .doctor import run_doctor
from .legacy_inputs import LegacyInputWorkflowError, run_legacy_input_workflow, write_input_manifest
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .validation.input_validator import InputValidator


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ohqbuild", description="Build OpenHydroQual OHQ files from GIS outputs.")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Build an OHQ file.")
    b.add_argument("--root", required=True)
    b.add_argument("--site", required=True)
    b.add_argument("--config", default=None)
    b.add_argument("--project-name", default=None)
    b.add_argument("--out", default=None)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--skip-input-check", action="store_true")
    b.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

    v = sub.add_parser("validate", help="Validate inputs and topology only.")
    v.add_argument("--root", required=True)
    v.add_argument("--site", required=True)
    v.add_argument("--config", default=None)
    v.add_argument("--skip-input-check", action="store_true")
    v.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

    prep = sub.add_parser(
        "prepare-inputs",
        help="Run retained QGIS preprocessing scripts to create GIStoOHQ input files.",
    )
    prep.add_argument("--root", required=True)
    prep.add_argument("--site", required=True)
    prep.add_argument("--script-dir", default=None)
    prep.add_argument("--phase", choices=["phase1", "phase2", "all"], default="all")

    init = sub.add_parser("init-inputs", help="Create source-input folders and an INPUTS.md checklist.")
    init.add_argument("--root", required=True)
    init.add_argument("--site", required=True)

    chk = sub.add_parser("check-inputs", help="Verify required GIStoOHQ input files and fields.")
    chk.add_argument("--root", required=True)
    chk.add_argument("--site", required=True)
    chk.add_argument("--config", default=None)
    chk.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")
    chk.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")

    run = sub.add_parser(
        "run",
        help="Prepare GIS inputs, validate them, and build the OHQ file in one workflow.",
    )
    run.add_argument("--root", required=True)
    run.add_argument("--site", required=True)
    run.add_argument("--config", default=None)
    run.add_argument("--project-name", default=None)
    run.add_argument("--out", default=None)
    run.add_argument("--script-dir", default=None)
    run.add_argument("--phase", choices=["phase1", "phase2", "all"], default="all")
    run.add_argument("--skip-prepare", action="store_true")
    run.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

    doctor = sub.add_parser("doctor", help="Check runtime, GIS, and legacy-script availability.")
    doctor.add_argument("--script-dir", default=None)
    doctor.add_argument("--strict-gis", action="store_true")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return p


def _print_input_result(result) -> None:
    for warning in result.warnings:
        print("WARNING:", warning)
    for error in result.errors:
        print("ERROR:", error)


def _validate_inputs(settings: BuilderSettings, no_schema: bool, json_output: bool = False) -> int:
    result = InputValidator().validate(settings, check_schema=not no_schema)
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        _print_input_result(result)
        if result.ok:
            print("Input validation OK")
    return 0 if result.ok else 2


def _maybe_validate_inputs(settings: BuilderSettings, skip_input_check: bool, no_schema: bool) -> int:
    if skip_input_check:
        return 0
    return _validate_inputs(settings, no_schema)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        settings = BuilderSettings.from_args(args.root, args.site, args.config, args.project_name)
        input_status = _maybe_validate_inputs(settings, args.skip_input_check, args.no_schema)
        if input_status != 0:
            return input_status
        out = Path(args.out).expanduser().resolve() if args.out else None
        result = build_ohq_project(settings, output_path=out, dry_run=args.dry_run)
        if result:
            print(result)
        return 0
    if args.command == "validate":
        settings = BuilderSettings.from_args(args.root, args.site, args.config)
        input_status = _maybe_validate_inputs(settings, args.skip_input_check, args.no_schema)
        if input_status != 0:
            return input_status
        build_ohq_project(settings, dry_run=True)
        return 0
    if args.command == "prepare-inputs":
        try:
            run_legacy_input_workflow(args.root, args.site, args.script_dir, args.phase)
        except LegacyInputWorkflowError as exc:
            print(f"prepare-inputs failed: {exc}")
            return 2
        return 0
    if args.command == "init-inputs":
        manifest = write_input_manifest(args.root, args.site)
        print(f"Created input folders and checklist: {manifest}")
        return 0
    if args.command == "check-inputs":
        settings = BuilderSettings.from_args(args.root, args.site, args.config)
        return _validate_inputs(settings, args.no_schema, args.json)
    if args.command == "run":
        if not args.skip_prepare:
            try:
                run_legacy_input_workflow(args.root, args.site, args.script_dir, args.phase)
            except LegacyInputWorkflowError as exc:
                print(f"prepare-inputs failed: {exc}")
                return 2
        settings = BuilderSettings.from_args(args.root, args.site, args.config, args.project_name)
        input_status = _validate_inputs(settings, args.no_schema)
        if input_status != 0:
            return input_status
        out = Path(args.out).expanduser().resolve() if args.out else None
        result = build_ohq_project(settings, output_path=out)
        if result:
            print(result)
        return 0
    if args.command == "doctor":
        report = run_doctor(args.script_dir, args.strict_gis)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            for line in report.lines():
                print(line)
        return 0 if report.ok else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from pathlib import Path

from .legacy_inputs import LegacyInputWorkflowError, run_legacy_input_workflow
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

    v = sub.add_parser("validate", help="Validate inputs and topology only.")
    v.add_argument("--root", required=True)
    v.add_argument("--site", required=True)
    v.add_argument("--config", default=None)

    prep = sub.add_parser(
        "prepare-inputs",
        help="Run retained QGIS preprocessing scripts to create GIStoOHQ input files.",
    )
    prep.add_argument("--root", required=True)
    prep.add_argument("--site", required=True)
    prep.add_argument("--script-dir", default=None)
    prep.add_argument("--phase", choices=["phase1", "phase2", "all"], default="all")

    chk = sub.add_parser("check-inputs", help="Verify required GIStoOHQ input files and fields.")
    chk.add_argument("--root", required=True)
    chk.add_argument("--site", required=True)
    chk.add_argument("--config", default=None)
    chk.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

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
    return p


def _print_input_result(result) -> None:
    for warning in result.warnings:
        print("WARNING:", warning)
    for error in result.errors:
        print("ERROR:", error)


def _validate_inputs(settings: BuilderSettings, no_schema: bool) -> int:
    result = InputValidator().validate(settings, check_schema=not no_schema)
    _print_input_result(result)
    if not result.ok:
        return 2
    print("Input validation OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        settings = BuilderSettings.from_args(args.root, args.site, args.config, args.project_name)
        out = Path(args.out).expanduser().resolve() if args.out else None
        result = build_ohq_project(settings, output_path=out, dry_run=args.dry_run)
        if result:
            print(result)
        return 0
    if args.command == "validate":
        settings = BuilderSettings.from_args(args.root, args.site, args.config)
        build_ohq_project(settings, dry_run=True)
        return 0
    if args.command == "prepare-inputs":
        try:
            run_legacy_input_workflow(args.root, args.site, args.script_dir, args.phase)
        except LegacyInputWorkflowError as exc:
            print(f"prepare-inputs failed: {exc}")
            return 2
        return 0
    if args.command == "check-inputs":
        settings = BuilderSettings.from_args(args.root, args.site, args.config)
        return _validate_inputs(settings, args.no_schema)
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

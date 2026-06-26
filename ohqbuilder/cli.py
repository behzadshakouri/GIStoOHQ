from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import build_ohq_project
from .settings import BuilderSettings


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
    return p


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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

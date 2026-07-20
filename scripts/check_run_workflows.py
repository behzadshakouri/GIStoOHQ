#!/usr/bin/env python3
"""Smoke-check the config-driven one-step and four-step run layouts.

This script intentionally uses ``run.py --dry-run`` so it validates the command
plans from 0 to 100 without requiring network access, QGIS, or downloaded data.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    (
        "one-step",
        "config.one-step.example.json",
        ("doctor", "full-run"),
    ),
    (
        "four-step",
        "config.four-step.example.json",
        ("doctor", "download-inputs", "materialize-inputs", "prepare-inputs", "build"),
    ),
)


def _run_dry_run(config_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "run.py", config_name, "--dry-run"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def main() -> int:
    for label, config_name, expected_steps in WORKFLOWS:
        result = _run_dry_run(config_name)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            sys.stderr.write(result.stdout)
            print(f"{label}: FAILED with exit code {result.returncode}", file=sys.stderr)
            return result.returncode

        missing = [step for step in expected_steps if f"Running {step}" not in result.stdout]
        if missing:
            print(
                f"{label}: dry run did not include expected step(s): {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.stderr.write(result.stdout)
            return 1

        print(f"{label}: OK")

    print("Both run.py approaches validate from start to finish in dry-run mode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

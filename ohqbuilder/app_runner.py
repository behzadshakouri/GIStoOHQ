from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

DEFAULT_CONFIG = "config.json"
EXAMPLE_CONFIG = "config.example.json"

REQUIRED_OUTPUTS = (
    "topology.gpkg",
    "subwatershed_params.gpkg",
    "reaches.gpkg",
    "junctions.gpkg",
)


class PipelineConfigError(ValueError):
    """Raised when the config-driven pipeline cannot load its configuration."""


@dataclass
class PipelineConfig:
    root: Path
    site: str
    config: str | None = None
    project_name: str | None = None
    out: str | None = None
    script_dir: str | None = None
    phase: str = "all"
    force: bool = False
    skip_prepare: bool = False
    no_schema: bool = False
    strict_gis: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "PipelineConfig":
        if "root" not in data or "site" not in data:
            raise ValueError("Pipeline config must include 'root' and 'site'.")
        return cls(
            root=Path(data["root"]).expanduser().resolve(),
            site=str(data["site"]),
            config=data.get("config"),
            project_name=data.get("project_name"),
            out=data.get("out"),
            script_dir=data.get("script_dir"),
            phase=str(data.get("phase", "all")),
            force=bool(data.get("force", False)),
            skip_prepare=bool(data.get("skip_prepare", False)),
            no_schema=bool(data.get("no_schema", False)),
            strict_gis=bool(data.get("strict_gis", False)),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "PipelineConfig":
        config_path = Path(path).expanduser().resolve()
        if not config_path.is_file():
            raise PipelineConfigError(f"Pipeline config not found: {config_path}")
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PipelineConfigError(f"Invalid JSON in {config_path}: {exc}") from exc
        return cls.from_mapping(data)

    @property
    def outputs_path(self) -> Path:
        return self.root / self.site / "outputs"

    def required_outputs_exist(self) -> bool:
        return all((self.outputs_path / filename).is_file() for filename in REQUIRED_OUTPUTS)


@dataclass
class PipelineStep:
    name: str
    command: list[str]
    skipped: bool = False
    reason: str = ""


@dataclass
class PipelineRunResult:
    returncode: int
    completed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed_step: str | None = None


def _base_command() -> list[str]:
    return [sys.executable, "-m", "ohqbuilder.cli"]


def _add_common_args(command: list[str], config: PipelineConfig) -> None:
    command.extend(["--root", str(config.root), "--site", config.site])
    if config.config:
        command.extend(["--config", config.config])


def build_steps(config: PipelineConfig) -> list[PipelineStep]:
    steps: list[PipelineStep] = []

    doctor = _base_command() + ["doctor"]
    if config.script_dir:
        doctor.extend(["--script-dir", config.script_dir])
    if config.strict_gis:
        doctor.append("--strict-gis")
    steps.append(PipelineStep("doctor", doctor))

    prepare = _base_command() + ["prepare-inputs", "--root", str(config.root), "--site", config.site]
    if config.script_dir:
        prepare.extend(["--script-dir", config.script_dir])
    prepare.extend(["--phase", config.phase])
    skip_prepare = config.skip_prepare or (config.required_outputs_exist() and not config.force)
    steps.append(
        PipelineStep(
            "prepare-inputs",
            prepare,
            skipped=skip_prepare,
            reason="required outputs already exist" if skip_prepare and not config.skip_prepare else "configured skip_prepare",
        )
    )

    check = _base_command() + ["check-inputs"]
    _add_common_args(check, config)
    if config.no_schema:
        check.append("--no-schema")
    steps.append(PipelineStep("check-inputs", check))

    build = _base_command() + ["build"]
    _add_common_args(build, config)
    if config.project_name:
        build.extend(["--project-name", config.project_name])
    if config.out:
        build.extend(["--out", config.out])
    if config.no_schema:
        build.append("--no-schema")
    steps.append(PipelineStep("build", build))
    return steps


def run_pipeline(config: PipelineConfig, dry_run: bool = False) -> PipelineRunResult:
    result = PipelineRunResult(returncode=0)
    steps = build_steps(config)
    total = len(steps)
    for index, step in enumerate(steps, start=1):
        if step.skipped:
            print(f"[{index}/{total}] Skipping {step.name}: {step.reason}")
            result.skipped.append(step.name)
            continue
        print(f"[{index}/{total}] Running {step.name}...")
        print("  " + " ".join(step.command))
        if dry_run:
            result.completed.append(step.name)
            continue
        completed = subprocess.run(step.command, cwd=Path.cwd())
        if completed.returncode != 0:
            print(f"ERROR: {step.name} failed with exit code {completed.returncode}")
            result.returncode = completed.returncode
            result.failed_step = step.name
            return result
        print(f"✓ {step.name} done")
        result.completed.append(step.name)
    print("✓ Pipeline finished")
    return result


def _maybe_create_config_from_example(config_path: Path) -> bool:
    example_path = Path(EXAMPLE_CONFIG).resolve()
    if config_path.name != DEFAULT_CONFIG or config_path.exists() or not example_path.is_file():
        return False
    shutil.copyfile(example_path, config_path)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the full GIStoOHQ pipeline from a config file.")
    parser.add_argument("config", nargs="?", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    if _maybe_create_config_from_example(config_path):
        print(f"Created {config_path} from {EXAMPLE_CONFIG}.")
        print("Edit root/site in that file, then rerun: python3 run.py config.json")
        return 2
    try:
        config = PipelineConfig.from_file(config_path)
    except PipelineConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Tip: copy {EXAMPLE_CONFIG} to {DEFAULT_CONFIG} and edit root/site.", file=sys.stderr)
        return 2
    return run_pipeline(config, dry_run=args.dry_run).returncode


if __name__ == "__main__":
    raise SystemExit(main())

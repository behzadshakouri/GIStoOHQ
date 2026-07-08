from __future__ import annotations

from pathlib import Path
from typing import Literal

from .qgis_env import ensure_processing_available

LegacyPhase = Literal["phase1", "phase2", "all"]

_PHASE_SCRIPTS = {
    "phase1": "run_phase1.py",
    "phase2": "run_phase2.py",
}


class LegacyInputWorkflowError(RuntimeError):
    """Raised when the legacy QGIS input-generation workflow cannot run."""


def default_script_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "legacy_gis"


def _require_qgis() -> None:
    try:
        import qgis.core  # noqa: F401
    except ImportError as exc:
        raise LegacyInputWorkflowError(
            "Creating GIS input files requires a QGIS Python environment. Open QGIS "
            "and run from its Python Console, or use a QGIS application Python "
            "environment, then rerun the prepare-inputs command."
        ) from exc
    if not ensure_processing_available():
        raise LegacyInputWorkflowError(
            "Creating GIS input files requires the QGIS processing plugin. The runner "
            "tried common QGIS plugin paths but could not import `processing`; run from "
            "the QGIS Python Console or set PYTHONPATH to QGIS's python/plugins folder."
        )


def _run_phase(script_path: Path, root: Path, site: str, script_dir: Path) -> None:
    if not script_path.is_file():
        raise LegacyInputWorkflowError(f"Legacy phase script not found: {script_path}")

    namespace = {
        "__name__": "__main__",
        "ROOT": str(root),
        "SITE_DIR": site,
        "SCRIPT_DIR": str(script_dir),
    }
    source = script_path.read_text(encoding="utf-8")
    exec(compile(source, str(script_path), "exec"), namespace)


def run_legacy_input_workflow(
    root: str | Path,
    site: str,
    script_dir: str | Path | None = None,
    phase: LegacyPhase = "all",
) -> None:
    """Run the retained QGIS scripts that create GIStoOHQ input GeoPackages.

    This is a thin package-level entry point for the legacy workflow. The actual
    GIS processing remains in ``scripts/legacy_gis/run_phase1.py`` and
    ``scripts/legacy_gis/run_phase2.py`` so generated files match the original
    script workflow.
    """

    if phase not in {"phase1", "phase2", "all"}:
        raise ValueError("phase must be one of: phase1, phase2, all")

    _require_qgis()

    root_path = Path(root).expanduser().resolve()
    script_path = Path(script_dir).expanduser().resolve() if script_dir else default_script_dir()
    phases = ("phase1", "phase2") if phase == "all" else (phase,)

    for selected_phase in phases:
        _run_phase(script_path / _PHASE_SCRIPTS[selected_phase], root_path, site, script_path)

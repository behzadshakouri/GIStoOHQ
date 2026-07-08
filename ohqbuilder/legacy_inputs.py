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


def required_inputs(root: Path, site: str, phase: LegacyPhase) -> list[tuple[Path, str]]:
    out_dir = root / site / "outputs"
    site_dir = root / site
    if phase == "phase1":
        return [
            (out_dir / "outlet.shp", "single-feature watershed outlet"),
            (site_dir / "demlr" / "cliped_utm.tif", "real-elevation DEM"),
            (out_dir / "NHDFlowline_clip.gpkg", "clipped NHD flowlines"),
        ]
    if phase == "phase2":
        return [
            (out_dir / "pour_points.shp", "hand-placed pour points"),
            (out_dir / "watershed_boundary.gpkg", "phase-1 watershed boundary"),
            (out_dir / "reaches.gpkg", "phase-1 reaches with topology"),
            (out_dir / "junctions.gpkg", "phase-1 junctions"),
            (out_dir / "outlet.shp", "phase-1 outlet"),
        ]
    return []


def check_required_inputs(root: Path, site: str, phase: LegacyPhase) -> None:
    missing = [(path, why) for path, why in required_inputs(root, site, phase) if not path.is_file()]
    if not missing:
        return
    lines = [f"Missing required {phase} input(s):"]
    lines.extend(f"  - {path} ({why})" for path, why in missing)
    lines.append("Create or download these inputs before running prepare-inputs.")
    raise LegacyInputWorkflowError("\n".join(lines))


def write_input_manifest(root: str | Path, site: str) -> Path:
    root_path = Path(root).expanduser().resolve()
    site_path = root_path / site
    outputs_path = site_path / "outputs"
    demlr_path = site_path / "demlr"
    outputs_path.mkdir(parents=True, exist_ok=True)
    demlr_path.mkdir(parents=True, exist_ok=True)
    manifest_path = site_path / "INPUTS.md"
    lines = [
        "# GIStoOHQ source inputs",
        "",
        "Place or generate these files before running `prepare-inputs`.",
        "",
        "## Phase 1",
    ]
    for path, why in required_inputs(root_path, site, "phase1"):
        lines.append(f"- `{path.relative_to(site_path)}` — {why}")
    lines.extend(["", "## Phase 2"])
    for path, why in required_inputs(root_path, site, "phase2"):
        lines.append(f"- `{path.relative_to(site_path)}` — {why}")
    lines.extend([
        "",
        "DEM/hydrography can be prepared upstream with tools such as DEMDownloader/demcheck.",
    ])
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


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
        check_required_inputs(root_path, site, selected_phase)
        _run_phase(script_path / _PHASE_SCRIPTS[selected_phase], root_path, site, script_path)

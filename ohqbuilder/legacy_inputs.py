from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .qgis_env import ensure_processing_available
from .pour_points import PourPointGenerationError, generate_pour_points
from .outlet_creator import OutletCreationError, create_outlet_from_flow_accumulation

LegacyPhase = Literal["phase1", "phase2", "all"]

_PHASE_SCRIPTS = {
    "phase1": "run_phase1.py",
    "phase2": "run_phase2.py",
}


@dataclass(frozen=True)
class LegacyWorkflowOptions:
    """Optional settings forwarded to the retained QGIS phase runners."""

    out_dir: str | Path | None = None
    dem_path: str | Path | None = None
    outlet_path: str | Path | None = None
    flowline_path: str | Path | None = None
    flowdir_path: str | Path | None = None
    flowacc_path: str | Path | None = None
    pour_points_path: str | Path | None = None
    watershed_path: str | Path | None = None
    reaches_path: str | Path | None = None
    junctions_path: str | Path | None = None
    target_epsg: int | str | None = None
    force: bool = True
    dry_run: bool = False
    child_options: dict[str, object] | None = None
    auto_pour_points: bool = True
    auto_outlet: bool = True


class LegacyInputWorkflowError(RuntimeError):
    """Raised when the legacy QGIS input-generation workflow cannot run."""


def default_script_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "legacy_gis"


def _module_available(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _require_qgis() -> None:
    if not _module_available("qgis.core"):
        raise LegacyInputWorkflowError(
            "Creating GIS input files requires a QGIS Python environment. Open QGIS "
            "and run from its Python Console, or use a QGIS application Python "
            "environment, then rerun the prepare-inputs command."
        )
    if not ensure_processing_available():
        raise LegacyInputWorkflowError(
            "Creating GIS input files requires the QGIS processing plugin. The runner "
            "tried common QGIS plugin paths but could not import `processing`; run from "
            "the QGIS Python Console or set PYTHONPATH to QGIS's python/plugins folder."
        )


def _resolve_optional(value: str | Path | None) -> Path | None:
    return Path(value).expanduser().resolve() if value is not None else None


def _default_site_path(root: Path, site: str) -> Path:
    site_path = Path(site).expanduser()
    if site_path.is_absolute():
        return site_path.resolve()
    return (root / site).resolve()


def _workflow_paths(root: Path, site: str, options: LegacyWorkflowOptions) -> dict[str, Path]:
    site_path = _default_site_path(root, site)
    out_dir = _resolve_optional(options.out_dir) or site_path / "outputs"
    return {
        "site_path": site_path,
        "out_dir": out_dir,
        "dem_path": _resolve_optional(options.dem_path) or site_path / "demlr" / "cliped_utm.tif",
        "outlet_path": _resolve_optional(options.outlet_path) or out_dir / "outlet.shp",
        "flowline_path": _resolve_optional(options.flowline_path) or out_dir / "NHDFlowline_clip.gpkg",
        "flowdir_path": _resolve_optional(options.flowdir_path) or out_dir / "flow_dir.tif",
        "flowacc_path": _resolve_optional(options.flowacc_path) or out_dir / "flow_acc.tif",
        "pour_points_path": _resolve_optional(options.pour_points_path) or out_dir / "pour_points.shp",
        "watershed_path": _resolve_optional(options.watershed_path) or out_dir / "watershed_boundary.gpkg",
        "reaches_path": _resolve_optional(options.reaches_path) or out_dir / "reaches.gpkg",
        "junctions_path": _resolve_optional(options.junctions_path) or out_dir / "junctions.gpkg",
    }


def _shapefile_components(path: Path) -> list[Path]:
    if path.suffix.lower() != ".shp":
        return [path]
    return [path.with_suffix(suffix) for suffix in (".shp", ".shx", ".dbf")]


def _input_exists(path: Path) -> bool:
    return all(component.is_file() for component in _shapefile_components(path))


def required_inputs(
    root: Path,
    site: str,
    phase: LegacyPhase,
    options: LegacyWorkflowOptions | None = None,
) -> list[tuple[Path, str]]:
    paths = _workflow_paths(root, site, options or LegacyWorkflowOptions())
    if phase == "phase1":
        return [
            (paths["outlet_path"], "single-feature watershed outlet"),
            (paths["dem_path"], "real-elevation DEM"),
            (paths["flowline_path"], "flowlines used for channel burning/reach extraction"),
            (paths["flowdir_path"], "flow-direction raster from hydrology preprocessing"),
            (paths["flowacc_path"], "flow-accumulation raster from hydrology preprocessing"),
        ]
    if phase == "phase2":
        return [
            (paths["pour_points_path"], "automatically generated or user-supplied pour points"),
            (paths["watershed_path"], "phase-1 watershed boundary"),
            (paths["reaches_path"], "phase-1 reaches with topology"),
            (paths["junctions_path"], "phase-1 junctions"),
            (paths["outlet_path"], "phase-1 outlet"),
        ]
    return []


def check_required_inputs(
    root: Path,
    site: str,
    phase: LegacyPhase,
    options: LegacyWorkflowOptions | None = None,
) -> None:
    missing: list[tuple[Path, str, list[Path]]] = []
    for path, why in required_inputs(root, site, phase, options):
        if not _input_exists(path):
            missing.append((path, why, [p for p in _shapefile_components(path) if not p.is_file()]))
    if not missing:
        return
    lines = [f"Missing required {phase} input(s):"]
    for path, why, components in missing:
        lines.append(f"  - {path} ({why})")
        if len(components) > 1 or components[0] != path:
            lines.extend(f"      missing component: {component}" for component in components)
    lines.append("Create or download these inputs before running prepare-inputs.")
    raise LegacyInputWorkflowError("\n".join(lines))


def write_input_manifest(root: str | Path, site: str) -> Path:
    root_path = Path(root).expanduser().resolve()
    site_path = _default_site_path(root_path, site)
    outputs_path = site_path / "outputs"
    demlr_path = site_path / "demlr"
    outputs_path.mkdir(parents=True, exist_ok=True)
    demlr_path.mkdir(parents=True, exist_ok=True)
    manifest_path = site_path / "INPUTS.md"
    options = LegacyWorkflowOptions()
    lines = [
        "# GIStoOHQ source inputs",
        "",
        "Place or generate these files before running `prepare-inputs`.",
        "",
        "## Phase 1",
    ]
    for path, why in required_inputs(root_path, site, "phase1", options):
        lines.append(f"- `{path.relative_to(site_path)}` — {why}")
    lines.extend(["", "## Phase 2"])
    for path, why in required_inputs(root_path, site, "phase2", options):
        lines.append(f"- `{path.relative_to(site_path)}` — {why}")
    lines.extend([
        "",
        "DEM/hydrography can be prepared upstream with tools such as DEMDownloader/demcheck.",
    ])
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def _namespace_for_phase(root: Path, site: str, script_dir: Path, options: LegacyWorkflowOptions) -> dict[str, object]:
    paths = _workflow_paths(root, site, options)
    namespace: dict[str, object] = {
        "__name__": "__main__",
        "ROOT": str(root),
        "SITE_DIR": site,
        "SITE_PATH": str(paths["site_path"]),
        "SCRIPT_DIR": str(script_dir),
        "OUT_DIR": str(paths["out_dir"]),
        "DEM_PATH": str(paths["dem_path"]),
        "OUTLET_PATH": str(paths["outlet_path"]),
        "FLOWLINE_PATH": str(paths["flowline_path"]),
        "FLOWDIR_PATH": str(paths["flowdir_path"]),
        "FLOWACC_PATH": str(paths["flowacc_path"]),
        "POUR_POINTS_PATH": str(paths["pour_points_path"]),
        "WATERSHED_PATH": str(paths["watershed_path"]),
        "REACHES_PATH": str(paths["reaches_path"]),
        "JUNCTIONS_PATH": str(paths["junctions_path"]),
        "FORCE": options.force,
        "DRY_RUN": options.dry_run,
    }
    if options.target_epsg is not None:
        namespace["TARGET_EPSG"] = int(options.target_epsg)
    if options.child_options:
        namespace["CHILD_OPTIONS"] = dict(options.child_options)
        namespace.update(options.child_options)
    return namespace


def _run_phase(script_path: Path, root: Path, site: str, script_dir: Path, options: LegacyWorkflowOptions) -> None:
    if not script_path.is_file():
        raise LegacyInputWorkflowError(f"Legacy phase script not found: {script_path}")

    namespace = _namespace_for_phase(root, site, script_dir, options)
    namespace["__file__"] = str(script_path)
    source = script_path.read_text(encoding="utf-8")
    exec(compile(source, str(script_path), "exec"), namespace)


def run_legacy_input_workflow(
    root: str | Path,
    site: str,
    script_dir: str | Path | None = None,
    phase: LegacyPhase = "all",
    options: LegacyWorkflowOptions | None = None,
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

    workflow_options = options or LegacyWorkflowOptions()
    root_path = Path(root).expanduser().resolve()
    script_path = Path(script_dir).expanduser().resolve() if script_dir else default_script_dir()
    phases = ("phase1", "phase2") if phase == "all" else (phase,)

    for selected_phase in phases:
        if selected_phase == "phase1" and workflow_options.auto_outlet:
            paths = _workflow_paths(root_path, site, workflow_options)
            if (
                not _input_exists(paths["outlet_path"])
                and _input_exists(paths["flowacc_path"])
            ):
                try:
                    result = create_outlet_from_flow_accumulation(
                        paths["flowacc_path"], paths["outlet_path"]
                    )
                except OutletCreationError as exc:
                    raise LegacyInputWorkflowError(
                        f"Automatic outlet creation failed: {exc}"
                    ) from exc
                print(
                    f"Created outlet at ({result.x:.3f}, {result.y:.3f}): "
                    f"{result.output_path}"
                )
        if selected_phase == "phase2" and workflow_options.auto_pour_points:
            paths = _workflow_paths(root_path, site, workflow_options)
            if not _input_exists(paths["pour_points_path"]):
                try:
                    result = generate_pour_points(
                        paths["junctions_path"], paths["pour_points_path"]
                    )
                except PourPointGenerationError as exc:
                    raise LegacyInputWorkflowError(
                        f"Automatic pour-point generation failed: {exc}"
                    ) from exc
                print(f"Generated {result.count} pour point(s): {result.output_path}")
        check_required_inputs(root_path, site, selected_phase, workflow_options)
        _run_phase(
            script_path / _PHASE_SCRIPTS[selected_phase],
            root_path,
            site,
            script_path,
            workflow_options,
        )

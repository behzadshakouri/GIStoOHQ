from __future__ import annotations

from pathlib import Path

from .builders.watershed_builder import WatershedBuilder
from .settings import BuilderSettings
from .validation.parameter_validator import ParameterValidator
from .validation.topology_validator import TopologyValidator
from .writers.hms_writer import HmsProjectResult, HmsWriter


def build_hms_project(
    settings: BuilderSettings, output_dir: str | Path | None = None
) -> HmsProjectResult:
    """Validate generated GIS inputs and write native HEC-HMS project files."""
    watershed = WatershedBuilder(settings).build()
    TopologyValidator().validate(watershed)
    ParameterValidator().validate(watershed)
    destination = Path(output_dir) if output_dir else settings.paths.outputs_path / "hec_hms"
    return HmsWriter().write(watershed, destination)


def validate_hms_project(project_file: str | Path) -> tuple[Path, ...]:
    """Validate the project index and all referenced native HEC-HMS files."""
    project = Path(project_file).expanduser().resolve()
    if not project.is_file():
        raise FileNotFoundError(f"HEC-HMS project file not found: {project}")
    references = []
    for line in project.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("Filename:"):
            referenced = project.parent / line.split(":", 1)[1].strip()
            if not referenced.is_file():
                raise FileNotFoundError(f"HEC-HMS referenced file not found: {referenced}")
            references.append(referenced)
    if len(references) != 4:
        raise ValueError(
            "HEC-HMS project must reference basin, meteorology, control, and run files."
        )
    return tuple(references)

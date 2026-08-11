from __future__ import annotations

from pathlib import Path

from .builders.watershed_builder import WatershedBuilder
from .builders.topology_builder import retain_topology_elements
from .logger import get_logger
from .settings import BuilderSettings
from .validation.topology_validator import TopologyValidator
from .validation.parameter_validator import ParameterValidator
from .writers.ohq_writer import OHQWriter

log = get_logger(__name__)


def build_ohq_project(
    settings: BuilderSettings, output_path: Path | None = None, dry_run: bool = False
) -> str | None:
    watershed = WatershedBuilder(settings).build()
    retain_topology_elements(watershed)
    TopologyValidator().validate(watershed)
    ParameterValidator().validate(watershed)
    log.info("Watershed summary: %s", watershed.summary())

    if dry_run:
        print(watershed.summary())
        return None

    if output_path is None:
        output_path = settings.paths.outputs_path / f"{settings.project_name}.ohq"
    output_path = Path(output_path)
    suffix = output_path.suffix or ".ohq"
    base = output_path.with_suffix("")
    legacy_path = base.with_name(f"{base.name}_legacy").with_suffix(suffix)
    mixed_hru_path = base.with_name(f"{base.name}_mixed_hru").with_suffix(suffix)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    OHQWriter(
        include_comments=settings.ohq.include_comments,
        formulation="legacy",
    ).write(watershed, legacy_path)
    OHQWriter(
        include_comments=settings.ohq.include_comments,
        formulation="mixed_hru",
    ).write(watershed, mixed_hru_path)
    log.info("Wrote legacy OHQ file: %s", legacy_path)
    log.info("Wrote mixed-HRU OHQ file: %s", mixed_hru_path)
    # Preserve the historical single-path return contract for callers while
    # both alternatives are always emitted together.
    return str(legacy_path)

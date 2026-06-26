from __future__ import annotations

from pathlib import Path

from .builders.watershed_builder import WatershedBuilder
from .logger import get_logger
from .settings import BuilderSettings
from .validation.topology_validator import TopologyValidator
from .validation.parameter_validator import ParameterValidator
from .writers.ohq_writer import OHQWriter

log = get_logger(__name__)


def build_ohq_project(settings: BuilderSettings, output_path: Path | None = None, dry_run: bool = False) -> str | None:
    watershed = WatershedBuilder(settings).build()
    TopologyValidator().validate(watershed)
    ParameterValidator().validate(watershed)
    log.info("Watershed summary: %s", watershed.summary())

    if dry_run:
        print(watershed.summary())
        return None

    if output_path is None:
        output_path = settings.paths.outputs_path / f"{settings.project_name}.ohq"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    OHQWriter(include_comments=settings.ohq.include_comments).write(watershed, output_path)
    log.info("Wrote OHQ file: %s", output_path)
    return str(output_path)

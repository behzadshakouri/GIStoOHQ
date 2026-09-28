from __future__ import annotations

from pathlib import Path
import os

from .builders.watershed_builder import WatershedBuilder
from .builders.topology_builder import retain_topology_elements
from .logger import get_logger
from .settings import BuilderSettings
from .validation.topology_validator import TopologyValidator
from .validation.parameter_validator import ParameterValidator
from .writers.ohq_writer import OHQWriter
from .channel_sections import load_channel_segments, write_fit_report

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
    standard_hru_path = base.with_name(f"{base.name}_standard_hru").with_suffix(suffix)
    detailed_hru_path = base.with_name(f"{base.name}_detailed_mixed_hru").with_suffix(suffix)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    OHQWriter(
        include_comments=settings.ohq.include_comments,
        formulation="legacy",
    ).write(watershed, legacy_path)
    OHQWriter(
        include_comments=settings.ohq.include_comments,
        formulation="mixed_hru",
    ).write(watershed, mixed_hru_path)
    OHQWriter(
        include_comments=settings.ohq.include_comments,
        formulation="standard_hru",
    ).write(watershed, standard_hru_path)
    profiles_file = os.environ.get("OHQ_CHANNEL_PROFILES_FILE", "").strip()
    if profiles_file:
        OHQWriter(
            include_comments=settings.ohq.include_comments,
            formulation="mixed_hru",
            channel_profiles_path=Path(profiles_file),
        ).write(watershed, detailed_hru_path)
        fit_report_path = base.with_name(f"{base.name}_channel_fit.csv")
        write_fit_report(fit_report_path,
                         load_channel_segments(profiles_file, {r.name: r for r in watershed.reaches}),
                         {r.name: r for r in watershed.reaches})
        log.info("Wrote detailed-channel mixed-HRU OHQ file: %s", detailed_hru_path)
    log.info("Wrote legacy OHQ file: %s", legacy_path)
    log.info("Wrote mixed-HRU OHQ file: %s", mixed_hru_path)
    log.info("Wrote standard-HRU OHQ file: %s", standard_hru_path)
    # Preserve the historical single-path return contract for callers while
    # all alternatives are emitted together.
    return str(legacy_path)

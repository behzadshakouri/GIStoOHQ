from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dem_materializer import materialize_dem
from .demcheck_adapter import download_with_demcheck, find_demcheck
from .hydro_materializer import materialize_flowlines
from .legacy_inputs import LegacyWorkflowOptions, run_hydrology_preprocessing, run_legacy_input_workflow
from .phase1_fetcher import fetch_phase1_inputs
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .validation.input_validator import InputValidator


class FullRunError(RuntimeError):
    """Raised when the download-to-OHQ workflow cannot finish."""


@dataclass(frozen=True)
class FullRunResult:
    output_path: Path


def run_full_pipeline(
    root: str | Path,
    site: str,
    *,
    lon: float,
    lat: float,
    project_name: str | None = None,
    output_path: str | Path | None = None,
    script_dir: str | Path | None = None,
    buffer_m: float = 5000.0,
    target_crs: str | None = None,
    demcheck_path: str | Path | None = None,
) -> FullRunResult:
    """Download, materialize, prepare, validate, and build a project in one call."""
    try:
        demcheck = find_demcheck(demcheck_path)
        if demcheck:
            fetched = download_with_demcheck(
                demcheck, root, site, lon=lon, lat=lat, buffer_m=buffer_m
            )
        else:
            fetched = fetch_phase1_inputs(
                root, site, lon=lon, lat=lat, products="all", buffer_m=buffer_m
            )
        dem = materialize_dem(root, site, source_dir=fetched.download_dir, dst_crs=target_crs)
        materialize_flowlines(
            root, site, source_dir=fetched.download_dir, dem_path=dem.output_path
        )
        options = LegacyWorkflowOptions(auto_outlet=True, auto_pour_points=True)
        run_hydrology_preprocessing(root, site, script_dir, options)
        run_legacy_input_workflow(root, site, script_dir, "all", options)
        settings = BuilderSettings.from_args(root, site, project_name=project_name)
        validation = InputValidator().validate(settings)
        if not validation.ok:
            raise FullRunError("Generated inputs failed validation: " + "; ".join(validation.errors))
        requested_output = Path(output_path).expanduser().resolve() if output_path else None
        built = build_ohq_project(settings, output_path=requested_output)
        if not built:
            raise FullRunError("OHQ builder did not produce an output path.")
        return FullRunResult(Path(built))
    except FullRunError:
        raise
    except Exception as exc:
        raise FullRunError(str(exc)) from exc

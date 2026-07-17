from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dem_materializer import materialize_dem
from .hydro_materializer import materialize_flowlines
from .legacy_inputs import LegacyWorkflowOptions, run_hydrology_preprocessing, run_legacy_input_workflow
from .input_downloader import download_all_inputs
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
    site_id: str | None = None,
    download_dir: str | Path | None = None,
    max_tiles: int | None = None,
    soil_pixel_size: float = 0.0003,
    soil_top_depth: float = 30.0,
) -> FullRunResult:
    """Download, materialize, prepare, validate, and build a project in one call."""
    try:
        # Step 1: download every supported source product before any merge/clip.
        fetched = download_all_inputs(
            root,
            site,
            lon=lon,
            lat=lat,
            site_id=site_id,
            download_dir=download_dir,
            buffer_m=buffer_m,
            max_tiles=max_tiles,
            soil_pixel_size=soil_pixel_size,
            soil_top_depth=soil_top_depth,
        )
        # Step 2: merge, project, and clip the downloaded DEM and hydrography.
        dem = materialize_dem(
            root, site, source_dir=fetched.product_dir("demlr"), dst_crs=target_crs
        )
        materialize_flowlines(
            root, site, source_dir=fetched.product_dir("hydro"), dem_path=dem.output_path
        )
        # Step 3: generate the GIS-derived model inputs.
        options = LegacyWorkflowOptions(auto_outlet=True, auto_pour_points=True)
        run_hydrology_preprocessing(root, site, script_dir, options)
        run_legacy_input_workflow(root, site, script_dir, "all", options)
        # Step 4: validate the generated inputs and write the OHQ file.
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

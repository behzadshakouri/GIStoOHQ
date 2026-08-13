from __future__ import annotations

import argparse
import json
import yaml
from pathlib import Path

from .dem_acquisition import (
    DemAcquisitionError,
    build_dem_tile_manifest,
    create_outlet_buffer_area,
    create_upstream_network_area,
    snap_outlet_to_flowlines,
    expand_acquisition_bounds,
    validate_watershed_within_acquisition,
)
from .dem_downloader import download_dem_manifest, parse_products, process_csv
from .dem_materializer import DemMaterializeError, materialize_dem
from .dem_workflow import (
    DemWorkflowError,
    prepare_dem_from_config,
    validate_dem_from_config,
    write_dem_config_template,
)
from .documented_watershed import (
    DocumentedWatershedError,
    REFERENCE_FILENAME,
    export_boundary_vertices,
    import_documented_watershed,
)
from .doctor import run_doctor
from .legacy_inputs import (
    LegacyInputWorkflowError,
    LegacyWorkflowOptions,
    run_hydrology_preprocessing,
    run_legacy_input_workflow,
    write_input_manifest,
)
from .phase1_fetcher import Phase1FetchError, fetch_phase1_inputs
from .pour_points import PourPointGenerationError, generate_pour_points
from .pour_point_candidates import PourPointCandidateError, promote_pour_point_candidates
from .report_baseline import (
    ReportBaselineError,
    compare_report_baseline,
    create_report_baseline,
)
from .outlet_creator import OutletCreationError, create_outlet_from_flow_accumulation
from .full_runner import FullRunError, run_full_pipeline
from .hms_pipeline import build_hms_project, validate_hms_project
from .input_downloader import download_all_inputs
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .soil_retrieval import (
    SoilRetrievalError,
    retrieve_hydrologic_soil_groups,
    retrieve_soil_texture,
)
from .source_materializer import materialize_source_inputs
from .validation.input_validator import InputValidator
from .watershed_bounds import WatershedBoundsError, resolve_materialization_bounds
from .watershed_data.schemas import SiteSpec, WatershedDataError
from .watershed_data.package import freeze_package, validate_package
from .watershed_data.reconnaissance import run_reconnaissance
from .watershed_data.usgs import acquire_observed_discharge
from .watershed_data.hydropinn import export_hydropinn
from .watershed_data.nasa_power import (
    DEFAULT_PARAMETERS,
    DEFAULT_PET_PARAMETERS,
    acquire_historical_meteorology,
    acquire_pet_et,
)
from .watershed_data.temporal import harmonize_asset
from .watershed_data.pipeline import run_watershed_data_pipeline
from .watershed_data.forecast import acquire_forecast_archive, materialize_available_forecasts
from .watershed_data.status import write_data_status
from .watershed_data.workflow import acquire_url, write_site_spec


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ohqbuild", description="Build OpenHydroQual OHQ files from GIS outputs."
    )
    sub = p.add_subparsers(dest="command", required=True)

    data = sub.add_parser(
        "data",
        help="Acquire and catalog optional provider-neutral watershed observations.",
    )
    data_sub = data.add_subparsers(dest="data_command", required=True)
    data_init = data_sub.add_parser("init-site", help="Create a generic watershed SiteSpec.")
    data_init.add_argument("--site-spec", required=True)
    data_init.add_argument("--site-id", required=True)
    data_init.add_argument("--name", default=None)
    data_init.add_argument("--lon", required=True, type=float)
    data_init.add_argument("--lat", required=True, type=float)
    data_init.add_argument("--start", required=True)
    data_init.add_argument("--end", required=True)
    data_init.add_argument("--force", action="store_true")
    data_validate = data_sub.add_parser("validate-site", help="Validate a watershed SiteSpec.")
    data_validate.add_argument("--site-spec", required=True)
    data_acquire = data_sub.add_parser(
        "acquire-url",
        help="Download one explicitly declared provider product into the immutable catalog.",
    )
    data_acquire.add_argument("--url", required=True)
    data_acquire.add_argument("--provider", required=True)
    data_acquire.add_argument("--product", required=True)
    data_acquire.add_argument("--product-version", default="unspecified")
    data_acquire.add_argument("--cache", required=True)
    data_acquire.add_argument("--catalog", required=True)
    data_freeze = data_sub.add_parser("freeze", help="Freeze a generic watershed package.")
    data_freeze.add_argument("--site-spec", required=True)
    data_freeze.add_argument("--catalog", required=True)
    data_freeze.add_argument("--output", required=True)
    data_freeze.add_argument(
        "--include-raw", choices=("none", "referenced", "all"), default="referenced"
    )
    data_freeze.add_argument("--object-store", default=None)
    data_freeze.add_argument("--redistributable", action="store_true")
    data_package = data_sub.add_parser("validate-package", help="Validate a frozen package.")
    data_package.add_argument("--package", required=True)
    data_recon = data_sub.add_parser(
        "reconnaissance", help="Discover and assess candidate USGS discharge gauges."
    )
    data_recon.add_argument("--site-spec", required=True)
    data_recon.add_argument("--output", required=True)
    data_recon.add_argument("--radius-km", type=float, default=50.0)
    data_discharge = data_sub.add_parser(
        "download-discharge", help="Download native USGS discharge for an explicit gauge."
    )
    data_discharge.add_argument("--site-spec", required=True)
    data_discharge.add_argument("--station-id", required=True)
    data_discharge.add_argument("--cache", required=True)
    data_discharge.add_argument("--catalog", required=True)
    data_weather = data_sub.add_parser(
        "download-weather", help="Download native NASA POWER hourly meteorology."
    )
    data_weather.add_argument("--site-spec", required=True)
    data_weather.add_argument("--cache", required=True)
    data_weather.add_argument("--catalog", required=True)
    data_weather.add_argument("--variables", default=",".join(DEFAULT_PARAMETERS))
    data_harmonize = data_sub.add_parser(
        "harmonize", help="Materialize a native temporal asset as a sorted UTC table with QC."
    )
    data_harmonize.add_argument("--asset-id", required=True)
    data_harmonize.add_argument("--catalog", required=True)
    data_harmonize.add_argument("--object-store", required=True)
    data_harmonize.add_argument("--qc-output", required=True)
    data_harmonize.add_argument("--provenance-output", required=True)
    data_pet = data_sub.add_parser("download-pet", help="Download native NASA POWER ET/PET data.")
    data_pet.add_argument("--site-spec", required=True)
    data_pet.add_argument("--cache", required=True)
    data_pet.add_argument("--catalog", required=True)
    data_pet.add_argument("--variables", default=",".join(DEFAULT_PET_PARAMETERS))
    data_export = data_sub.add_parser("export-hydropinn", help="Export a HydroPINN profile.")
    data_export.add_argument("--package", required=True)
    data_export.add_argument("--object-store", default=None)
    data_export.add_argument("--output", required=True)
    data_export.add_argument("--profile", default="water-balance-v1")
    data_run = data_sub.add_parser(
        "run", help="Download selected products, harmonize/QC, and freeze a package."
    )
    data_run.add_argument("--site-spec", required=True)
    data_run.add_argument("--station-id", default="")
    data_run.add_argument("--workspace", required=True)
    data_run.add_argument("--no-discharge", action="store_true")
    data_run.add_argument("--no-weather", action="store_true")
    data_run.add_argument("--no-pet", action="store_true")
    data_run.add_argument("--export-hydropinn", action="store_true")
    forecast = data_sub.add_parser("download-forecast", help="Acquire a versioned forecast archive.")
    forecast.add_argument("--url", required=True)
    forecast.add_argument("--provider", required=True)
    forecast.add_argument("--product", required=True)
    forecast.add_argument("--cache", required=True)
    forecast.add_argument("--catalog", required=True)
    forecast_view = data_sub.add_parser(
        "forecast-view", help="Create a leakage-safe forecast view at a prediction time."
    )
    forecast_view.add_argument("--asset-id", required=True)
    forecast_view.add_argument("--prediction-time", required=True)
    forecast_view.add_argument("--object-store", required=True)
    forecast_view.add_argument("--catalog", required=True)
    data_status = data_sub.add_parser("status", help="List catalog assets and object availability.")
    data_status.add_argument("--catalog", required=True)
    data_status.add_argument("--object-store", default=None)
    data_status.add_argument("--output", required=True)

    b = sub.add_parser("build", help="Build an OHQ file.")
    b.add_argument("--root", required=True)
    b.add_argument("--site", required=True)
    b.add_argument("--config", default=None)
    b.add_argument("--project-name", default=None)
    b.add_argument("--out", default=None)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--skip-input-check", action="store_true")
    b.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

    hms = sub.add_parser("build-hms", help="Build native HEC-HMS project files from GIS outputs.")
    hms.add_argument("--root", required=True)
    hms.add_argument("--site", required=True)
    hms.add_argument("--config", default=None)
    hms.add_argument("--project-name", default=None)
    hms.add_argument("--out-dir", default=None)

    hms_validate = sub.add_parser("validate-hms", help="Validate HEC-HMS project references.")
    hms_validate.add_argument("--project", required=True)

    v = sub.add_parser("validate", help="Validate inputs and topology only.")
    v.add_argument("--root", required=True)
    v.add_argument("--site", required=True)
    v.add_argument("--config", default=None)
    v.add_argument("--skip-input-check", action="store_true")
    v.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

    prep = sub.add_parser(
        "prepare-inputs",
        help="Run retained QGIS preprocessing scripts to create GIStoOHQ input files.",
    )
    prep.add_argument("--root", required=True)
    prep.add_argument("--site", required=True)
    prep.add_argument("--script-dir", default=None)
    prep.add_argument("--phase", choices=["phase1", "phase2", "all"], default="all")
    prep.add_argument(
        "--out-dir",
        default=None,
        help="Legacy outputs directory; defaults to <root>/<site>/outputs.",
    )
    prep.add_argument(
        "--dem-path", default=None, help="Real-elevation DEM path passed to Phase 1 scripts."
    )
    prep.add_argument(
        "--outlet-path", default=None, help="Outlet shapefile path passed to legacy scripts."
    )
    prep.add_argument(
        "--flowline-path", default=None, help="Flowline path passed to legacy scripts."
    )
    prep.add_argument(
        "--flowdir-path", default=None, help="flow_dir.tif path passed to Phase 1 scripts."
    )
    prep.add_argument(
        "--flowacc-path", default=None, help="flow_acc.tif path passed to Phase 1 scripts."
    )
    prep.add_argument(
        "--target-epsg", default=None, help="Target EPSG code forwarded to legacy scripts."
    )
    prep.add_argument(
        "--no-force", action="store_true", help="Forward FORCE=False to legacy scripts."
    )
    prep.add_argument(
        "--dry-run",
        action="store_true",
        help="Run legacy preflight and list steps without executing processing.",
    )
    prep.add_argument(
        "--start-at",
        default=None,
        help="Resume a phase at the named legacy step script, e.g. load_cn_inputs.py.",
    )
    prep.add_argument(
        "--no-auto-pour-points",
        action="store_true",
        help="Require an existing pour_points.shp instead of generating it from Phase 1 junctions.",
    )
    prep.add_argument(
        "--no-auto-outlet",
        action="store_true",
        help="Require an existing outlet.shp instead of deriving it from flow_acc.tif.",
    )

    hydro_prep = sub.add_parser(
        "prepare-hydrology",
        help="Create flow_dir.tif and flow_acc.tif from materialized DEM and hydrography.",
    )
    hydro_prep.add_argument("--root", required=True)
    hydro_prep.add_argument("--site", required=True)
    hydro_prep.add_argument("--script-dir", default=None)
    hydro_prep.add_argument(
        "--out-dir",
        default=None,
        help="Legacy outputs directory; defaults to <root>/<site>/outputs.",
    )
    hydro_prep.add_argument(
        "--dem-path", default=None, help="DEM path passed to hydrology preprocessing."
    )
    hydro_prep.add_argument(
        "--flowline-path", default=None, help="Flowline path passed to hydrology preprocessing."
    )
    hydro_prep.add_argument("--flowdir-path", default=None, help="flow_dir.tif output path.")
    hydro_prep.add_argument("--flowacc-path", default=None, help="flow_acc.tif output path.")
    hydro_prep.add_argument(
        "--target-epsg", default=None, help="Target EPSG code forwarded to legacy scripts."
    )
    hydro_prep.add_argument(
        "--no-force", action="store_true", help="Forward FORCE=False to legacy scripts."
    )
    hydro_prep.add_argument(
        "--dry-run",
        action="store_true",
        help="Run preflight and list steps without executing processing.",
    )

    outlet = sub.add_parser(
        "create-outlet",
        help="Create outlet.shp at the maximum flow-accumulation cell.",
    )
    outlet.add_argument("--root", required=True)
    outlet.add_argument("--site", required=True)
    outlet.add_argument(
        "--flow-acc", default=None, help="Defaults to <root>/<site>/outputs/flow_acc.tif."
    )
    outlet.add_argument("--out", default=None, help="Defaults to <root>/<site>/outputs/outlet.shp.")
    outlet.add_argument("--overwrite", action="store_true")

    pour = sub.add_parser(
        "create-pour-points",
        help=("Create Phase 2 pour_points.shp from flow-accumulation ranks around "
              "Phase 1 junctions."),
    )
    pour.add_argument("--root", required=True)
    pour.add_argument("--site", required=True)
    pour.add_argument(
        "--junctions", default=None, help="Defaults to <root>/<site>/outputs/junctions.gpkg."
    )
    pour.add_argument(
        "--flow-acc", default=None, help="Defaults to <root>/<site>/outputs/flow_acc.tif."
    )
    pour.add_argument(
        "--out", default=None, help="Defaults to <root>/<site>/outputs/pour_points.shp."
    )
    pour.add_argument(
        "--fallback-outlet",
        default=None,
        help="Used as the sole pour point when a single-reach watershed has no junctions; "
        "defaults to <root>/<site>/outputs/outlet.shp.",
    )
    pour.add_argument("--overwrite", action="store_true")

    promote = sub.add_parser(
        "promote-pour-points",
        help="Validate approved review candidates and write Phase 2 pour_points.shp.",
    )
    promote.add_argument("--root", required=True)
    promote.add_argument("--site", required=True)
    promote.add_argument("--candidates", default=None)
    promote.add_argument("--boundary", default=None)
    promote.add_argument("--out", default=None)
    promote.add_argument("--minimum-spacing-m", type=float, default=100.0)
    promote.add_argument("--overwrite", action="store_true")

    documented = sub.add_parser(
        "import-watershed-reference",
        help="Import a cited local/ArcGIS named-watershed polygon for boundary QA.",
    )
    documented.add_argument("--root", required=True)
    documented.add_argument("--site", required=True)
    documented.add_argument(
        "--source",
        required=True,
        help="Local vector path or ArcGIS FeatureServer/MapServer numeric layer URL.",
    )
    documented.add_argument("--layer", default=None, help="Layer name for a local container.")
    documented.add_argument("--name-field", default=None)
    documented.add_argument("--name", default=None, help="Exact named-watershed value.")
    documented.add_argument("--where", default="1=1", help="ArcGIS attribute filter.")
    documented.add_argument("--lon", type=float, required=True)
    documented.add_argument("--lat", type=float, required=True)
    documented.add_argument("--source-title", required=True)
    documented.add_argument("--source-organization", required=True)
    documented.add_argument("--source-url", default=None)
    documented.add_argument("--license", dest="license_text", default=None)
    documented.add_argument(
        "--allow-outlet-outside",
        action="store_true",
        help="Import the selected reference even when it does not contain the modeled outlet.",
    )
    documented.add_argument("--out", default=None)

    vertices = sub.add_parser(
        "export-watershed-coordinates",
        help="Export all polygon boundary vertices to CSV without dropping parts or holes.",
    )
    vertices.add_argument("--source", required=True)
    vertices.add_argument("--out", required=True)
    vertices.add_argument("--layer", default=None)
    vertices.add_argument(
        "--target-crs",
        default=None,
        help="Optional output CRS (for example EPSG:26918 or EPSG:4326).",
    )

    dl = sub.add_parser(
        "download-data",
        help="Query/download USGS DEM and hydrography products for site coordinates.",
    )
    dl.add_argument("input_csv", help="CSV with WGS84 latitude/longitude columns.")
    dl.add_argument("output_csv", nargs="?", default=None, help="Optional CSV summary to write.")
    dl.add_argument(
        "--products",
        default="dem",
        help="dem/demhr, demlr, hydro, wbd, roads, landcover/nlcd, atlas14, all, or a comma-separated subset (default: dem).",
    )
    dl.add_argument("--download", default=None, help="Directory for per-site downloads.")
    dl.add_argument("--id-col", default=None, help="Column used for per-site folder names.")
    dl.add_argument("--lat-col", default=None, help="Latitude column (auto-detected by default).")
    dl.add_argument("--lon-col", default=None, help="Longitude column (auto-detected by default).")
    dl.add_argument("--buffer", type=float, default=30.0, help="Half-width of query box in meters.")
    dl.add_argument(
        "--max-tiles", type=int, default=None, help="Cap files per product/site; 0 means no cap."
    )
    dl.add_argument(
        "--max-file-size-mb",
        type=float,
        default=512.0,
        help="Maximum single download size in MiB; 0 disables the size guard.",
    )
    dl.add_argument(
        "--dem-resolution",
        default="1/3",
        help="DEM tier for product dem: 1/3, 1/9, 1m, 30m, or auto (default: 1/3).",
    )
    dl.add_argument(
        "--make-points", action="store_true", help="Write a single-point shapefile per site."
    )
    dl.add_argument(
        "--points-dir",
        default=None,
        help="Base directory for point shapefiles; defaults to --download when set.",
    )
    dl.add_argument(
        "--tiger-year", type=int, default=2025, help="Census TIGER/Line vintage year for roads."
    )
    dl.add_argument("--nlcd-year", type=int, default=2023, help="Annual NLCD land-cover year.")

    manifest_download = sub.add_parser(
        "download-dem-manifest",
        help="Download URL-backed DEM manifest items and update tile paths.",
    )
    manifest_download.add_argument(
        "--manifest", required=True, help="DEM manifest JSON with URL-backed items."
    )
    manifest_download.add_argument(
        "--out-dir", required=True, help="Directory for downloaded raw DEM tiles."
    )
    manifest_download.add_argument(
        "--updated-manifest",
        default=None,
        help="Optional output manifest path; defaults to updating --manifest.",
    )

    hsg = sub.add_parser("download-hsg", help="Retrieve USDA SDA hydrologic soil group products.")
    hsg.add_argument("--root", required=True)
    hsg.add_argument("--site", required=True)
    hsg.add_argument("--buffer", type=float, default=5000.0)
    hsg.add_argument("--pixel-size", type=float, default=0.0003)

    texture = sub.add_parser("download-texture", help="Retrieve USDA SDA soil texture products.")
    texture.add_argument("--root", required=True)
    texture.add_argument("--site", required=True)
    texture.add_argument("--buffer", type=float, default=5000.0)
    texture.add_argument("--pixel-size", type=float, default=0.0003)
    texture.add_argument("--top-depth", type=float, default=30.0)

    mat_dem = sub.add_parser(
        "materialize-dem",
        help="Mosaic/reproject downloaded DEM rasters to demlr/cliped_utm.tif.",
    )
    mat_dem.add_argument("--root", required=True)
    mat_dem.add_argument("--site", required=True)
    mat_dem.add_argument(
        "--source-dir", default=None, help="Directory containing downloaded DEM rasters/zips."
    )
    mat_dem.add_argument(
        "--out",
        default=None,
        help="Output DEM path; defaults to <root>/<site>/demlr/cliped_utm.tif.",
    )
    mat_dem.add_argument(
        "--dst-crs",
        default=None,
        help="Target CRS, e.g. EPSG:26912; defaults to UTM inferred from raster center.",
    )
    mat_dem.add_argument(
        "--manifest",
        default=None,
        help="DEM download manifest with an explicit tiles list; avoids scanning unrelated rasters.",
    )

    area = sub.add_parser(
        "dem-acquisition-area",
        help="Create an outlet-based initial DEM acquisition polygon for downloader tile selection.",
    )
    area.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    area.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    area.add_argument(
        "--out", required=True, help="Output GeoJSON path for the acquisition polygon."
    )
    area.add_argument(
        "--upstream-km", type=float, default=25.0, help="Distance from outlet toward upstream end."
    )
    area.add_argument(
        "--downstream-km", type=float, default=3.0, help="Small downstream margin below the outlet."
    )
    area.add_argument("--lateral-km", type=float, default=5.0, help="Half-width lateral margin.")
    area.add_argument(
        "--azimuth",
        type=float,
        default=None,
        help="Optional upstream azimuth, degrees clockwise from north, for an oriented rectangle.",
    )

    snap = sub.add_parser(
        "dem-snap-outlet",
        help="Snap an outlet point to the nearest GeoJSON flowline segment.",
    )
    snap.add_argument("--lat", type=float, required=True, help="Raw outlet latitude in EPSG:4326.")
    snap.add_argument("--lon", type=float, required=True, help="Raw outlet longitude in EPSG:4326.")
    snap.add_argument("--flowlines", required=True, help="EPSG:4326 GeoJSON flowlines.")
    snap.add_argument(
        "--out", required=True, help="Output GeoJSON path for the snapped outlet point."
    )
    snap.add_argument(
        "--snap-distance-m",
        type=float,
        default=500.0,
        help="Maximum allowed snap distance in meters.",
    )

    network_area = sub.add_parser(
        "dem-upstream-network-area",
        help="Create a lightweight upstream-flowline DEM acquisition envelope.",
    )
    network_area.add_argument(
        "--lat", type=float, required=True, help="Outlet latitude in EPSG:4326."
    )
    network_area.add_argument(
        "--lon", type=float, required=True, help="Outlet longitude in EPSG:4326."
    )
    network_area.add_argument(
        "--flowlines",
        required=True,
        help="EPSG:4326 GeoJSON flowlines used to infer the upstream envelope.",
    )
    network_area.add_argument(
        "--out", required=True, help="Output GeoJSON path for the acquisition polygon."
    )
    network_area.add_argument(
        "--upstream-trace-km",
        type=float,
        default=40.0,
        help="Maximum outlet-to-flowline vertex distance to consider.",
    )
    network_area.add_argument(
        "--upstream-margin-km",
        type=float,
        default=5.0,
        help="Safety margin beyond the upstream flowline extent.",
    )
    network_area.add_argument(
        "--downstream-margin-km",
        type=float,
        default=3.0,
        help="Safety margin downstream of the outlet.",
    )
    network_area.add_argument(
        "--lateral-margin-km",
        type=float,
        default=4.0,
        help="Safety margin on both sides of the flowline envelope.",
    )
    network_area.add_argument(
        "--envelope-type",
        default="oriented_rectangle",
        choices=("oriented_rectangle", "axis_aligned_rectangle"),
    )

    manifest = sub.add_parser(
        "dem-tile-manifest",
        help="Select DEM tile-index features intersecting a DEM acquisition polygon.",
    )
    manifest.add_argument(
        "--acquisition-area",
        required=True,
        help="GeoJSON acquisition polygon from dem-acquisition-area or UI drawing.",
    )
    manifest.add_argument(
        "--tile-index",
        required=True,
        help="GeoJSON tile footprint/index file with URL/path properties.",
    )
    manifest.add_argument("--out", required=True, help="Output DEM download manifest JSON.")
    manifest.add_argument(
        "--url-field", default="url", help="Tile-index property containing the download URL."
    )
    manifest.add_argument(
        "--path-field",
        default="path",
        help="Tile-index property containing the local raw tile path.",
    )

    boundary = sub.add_parser(
        "dem-boundary-check",
        help="Check whether a delineated watershed is too close to the DEM acquisition boundary.",
    )
    boundary.add_argument(
        "--watershed", required=True, help="Delineated watershed GeoJSON polygon."
    )
    boundary.add_argument(
        "--acquisition-area", required=True, help="DEM acquisition GeoJSON polygon."
    )
    boundary.add_argument("--safety-distance-m", type=float, default=500.0)
    boundary.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")

    expand = sub.add_parser(
        "dem-expand-area",
        help="Directionally expand a DEM acquisition polygon after a boundary check fails.",
    )
    expand.add_argument(
        "--acquisition-area", required=True, help="DEM acquisition GeoJSON polygon to expand."
    )
    expand.add_argument(
        "--out", required=True, help="Output expanded DEM acquisition GeoJSON polygon."
    )
    expand.add_argument(
        "--edges", required=True, help="Comma-separated edges to expand: west,south,east,north."
    )
    expand.add_argument("--expansion-distance-km", type=float, default=5.0)

    fetch = sub.add_parser(
        "fetch-phase1-inputs",
        help="Create outlet.shp and download source DEM/hydro products for phase 1.",
    )
    fetch.add_argument("--root", required=True)
    fetch.add_argument("--site", required=True)
    fetch.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    fetch.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    fetch.add_argument(
        "--site-id",
        default=None,
        help="Folder-safe ID for source downloads; defaults to the site name.",
    )
    fetch.add_argument(
        "--products",
        default="all",
        help="dem, demlr, hydro, wbd, roads, landcover/nlcd, atlas14, all, or comma-separated subset (default: all).",
    )
    fetch.add_argument(
        "--download-dir",
        default=None,
        help="Raw source download directory; defaults under the site folder.",
    )
    fetch.add_argument(
        "--buffer", type=float, default=500.0, help="Half-width of TNM query box in meters."
    )
    fetch.add_argument(
        "--max-tiles", type=int, default=None, help="Cap files per product/site; 0 means no cap."
    )
    fetch.add_argument(
        "--max-file-size-mb",
        type=float,
        default=512.0,
        help="Maximum single download size in MiB; 0 disables the size guard.",
    )
    fetch.add_argument(
        "--skip-outlet",
        action="store_true",
        help="Only create folders and download source products.",
    )

    all_inputs = sub.add_parser(
        "download-inputs",
        help="Download C++-parity source products plus HSG and soil texture before merge/clip.",
    )
    all_inputs.add_argument("--root", required=True)
    all_inputs.add_argument("--site", required=True)
    all_inputs.add_argument("--lat", type=float, required=True)
    all_inputs.add_argument("--lon", type=float, required=True)
    all_inputs.add_argument("--site-id", default=None)
    all_inputs.add_argument("--download-dir", default=None)
    all_inputs.add_argument("--buffer", type=float, default=5000.0)
    all_inputs.add_argument("--max-tiles", type=int, default=None)
    all_inputs.add_argument(
        "--max-file-size-mb",
        type=float,
        default=512.0,
        help="Maximum single download size in MiB; 0 disables the size guard.",
    )
    all_inputs.add_argument("--soil-pixel-size", type=float, default=0.0003)
    all_inputs.add_argument("--soil-top-depth", type=float, default=30.0)

    materialize = sub.add_parser(
        "materialize-inputs",
        help="Merge/project DEM and extract/clip hydrography in one step.",
    )
    materialize.add_argument("--root", required=True)
    materialize.add_argument("--site", required=True)
    materialize.add_argument("--source-dir", default=None)
    materialize.add_argument("--target-crs", default=None)
    materialize.add_argument(
        "--dem-manifest", default=None, help="DEM tile manifest with explicit raw raster paths."
    )
    materialize.add_argument(
        "--clip-bounds", default=None, help="Optional minx,miny,maxx,maxy materialization bounds."
    )
    materialize.add_argument(
        "--clip-bounds-crs",
        default="EPSG:4326",
        help="CRS for --clip-bounds; defaults to EPSG:4326.",
    )
    materialize.add_argument(
        "--clip-center-lat",
        type=float,
        default=None,
        help="Latitude for auto materialization bounds.",
    )
    materialize.add_argument(
        "--clip-center-lon",
        type=float,
        default=None,
        help="Longitude for auto materialization bounds.",
    )
    materialize.add_argument(
        "--clip-buffer",
        type=float,
        default=None,
        help="Meter buffer around --clip-center-lat/lon for materialization bounds.",
    )
    materialize.add_argument(
        "--clip-buffer-scale",
        type=float,
        default=1.2,
        help="Safety scale applied to --clip-buffer; default 1.2.",
    )

    init_dem = sub.add_parser(
        "init-dem-config",
        help="Write a starter DEM acquisition config from outlet and optional flowline/tile-index paths.",
    )
    init_dem.add_argument("--config", required=True, help="Output YAML/JSON config path.")
    init_dem.add_argument("--site", required=True, help="Site/project name.")
    init_dem.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    init_dem.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    init_dem.add_argument(
        "--flowlines", default=None, help="GeoJSON flowlines for upstream_network mode."
    )
    init_dem.add_argument(
        "--tile-index", default=None, help="Optional DEM tile-index GeoJSON path."
    )
    init_dem.add_argument(
        "--target-crs",
        default=None,
        help="Optional target CRS; defaults to NAD83 UTM inferred from outlet.",
    )
    init_dem.add_argument(
        "--method",
        default="upstream_network",
        choices=("upstream_network", "outlet_buffer", "oriented_outlet_buffer", "polygon"),
    )
    init_dem.add_argument(
        "--force", action="store_true", help="Replace an existing config after explicit review."
    )
    init_dem.add_argument(
        "--demo-inputs",
        action="store_true",
        help="Explicitly use bundled Sligo smoke-test flowline and tile-index inputs.",
    )

    run_dem_prep = sub.add_parser(
        "run-dem-prep",
        help="Run the direct DEM prep path from one config, with optional download/materialization.",
    )
    run_dem_prep.add_argument("--config", required=True, help="YAML/JSON project config.")
    run_dem_prep.add_argument(
        "--download",
        action="store_true",
        help="Download URL-backed DEM manifest tiles after prepare-dem.",
    )
    run_dem_prep.add_argument(
        "--materialize", action="store_true", help="Run materialize-inputs after optional download."
    )
    run_dem_prep.add_argument(
        "--validate",
        action="store_true",
        help="Run validate-dem after prepare/download/materialize.",
    )

    prepare_dem = sub.add_parser(
        "prepare-dem",
        help="Create DEM acquisition area and tile manifest from a project config.",
    )
    prepare_dem.add_argument("--config", required=True, help="YAML/JSON project config.")

    validate_dem = sub.add_parser(
        "validate-dem",
        help="Validate watershed clearance from a project config and optionally expand DEM area.",
    )
    validate_dem.add_argument("--config", required=True, help="YAML/JSON project config.")

    bounds = sub.add_parser(
        "watershed-bounds",
        help="Resolve web watershed bounds from USGS NLDI, with coordinate-buffer fallback.",
    )
    bounds.add_argument("--lat", type=float, required=True)
    bounds.add_argument("--lon", type=float, required=True)
    bounds.add_argument("--buffer", type=float, default=20000.0)
    bounds.add_argument("--safety-scale", type=float, default=1.2)
    bounds.add_argument("--timeout", type=float, default=20.0)
    bounds.add_argument(
        "--no-web", action="store_true", help="Skip NLDI and use coordinate-buffer bounds."
    )
    bounds.add_argument("--json", action="store_true")

    init = sub.add_parser(
        "init-inputs", help="Create source-input folders and an INPUTS.md checklist."
    )
    init.add_argument("--root", required=True)
    init.add_argument("--site", required=True)

    chk = sub.add_parser("check-inputs", help="Verify required GIStoOHQ input files and fields.")
    chk.add_argument("--root", required=True)
    chk.add_argument("--site", required=True)
    chk.add_argument("--config", default=None)
    chk.add_argument(
        "--no-schema", action="store_true", help="Only check that required files exist."
    )
    chk.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")

    run = sub.add_parser(
        "run",
        help="Prepare GIS inputs, validate them, and build the OHQ file in one workflow.",
    )
    run.add_argument("--root", required=True)
    run.add_argument("--site", required=True)
    run.add_argument("--config", default=None)
    run.add_argument("--project-name", default=None)
    run.add_argument("--out", default=None)
    run.add_argument("--script-dir", default=None)
    run.add_argument("--phase", choices=["phase1", "phase2", "all"], default="all")
    run.add_argument(
        "--out-dir",
        default=None,
        help="Legacy outputs directory; defaults to <root>/<site>/outputs.",
    )
    run.add_argument(
        "--dem-path", default=None, help="Real-elevation DEM path passed to Phase 1 scripts."
    )
    run.add_argument(
        "--outlet-path", default=None, help="Outlet shapefile path passed to legacy scripts."
    )
    run.add_argument(
        "--flowline-path", default=None, help="Flowline path passed to legacy scripts."
    )
    run.add_argument(
        "--flowdir-path", default=None, help="flow_dir.tif path passed to Phase 1 scripts."
    )
    run.add_argument(
        "--flowacc-path", default=None, help="flow_acc.tif path passed to Phase 1 scripts."
    )
    run.add_argument(
        "--target-epsg", default=None, help="Target EPSG code forwarded to legacy scripts."
    )
    run.add_argument(
        "--no-force", action="store_true", help="Forward FORCE=False to legacy scripts."
    )
    run.add_argument(
        "--prepare-dry-run",
        action="store_true",
        help="Run legacy preflight and list steps without executing processing.",
    )
    run.add_argument(
        "--start-at", default=None, help="Resume prepare phase at the named legacy step script."
    )
    run.add_argument("--skip-prepare", action="store_true")
    run.add_argument(
        "--no-schema", action="store_true", help="Only check that required files exist."
    )
    run.add_argument(
        "--no-auto-pour-points", action="store_true", help="Require manually supplied pour points."
    )
    run.add_argument(
        "--no-auto-outlet", action="store_true", help="Require a manually supplied outlet."
    )

    full = sub.add_parser(
        "full-run",
        help="Download source data and build an OHQ project in one command.",
    )
    full.add_argument("--root", required=True)
    full.add_argument("--site", required=True)
    full.add_argument("--lat", type=float, default=None, help="Fallback outlet latitude.")
    full.add_argument("--lon", type=float, default=None, help="Fallback outlet longitude.")
    full.add_argument("--outlet-source", default=None, help="KML/KMZ point file for the modeled outlet.")
    full.add_argument(
        "--snap-outlet-to-documented-watershed",
        action="store_true",
        help="Move an outlet outside the documented watershed to the nearest boundary point.",
    )
    full.add_argument("--project-name", default=None)
    full.add_argument("--out", default=None)
    full.add_argument(
        "--config",
        default=None,
        help="Optional YAML/JSON config used to fill documented_watershed defaults.",
    )
    full.add_argument("--script-dir", default=None)
    full.add_argument(
        "--buffer", type=float, default=None,
        help="Source-data query buffer in meters; defaults to the acquisition area radius, or 5000 m without an area."
    )
    full.add_argument(
        "--target-crs", default=None, help="Optional DEM target CRS, e.g. EPSG:26912."
    )
    full.add_argument("--site-id", default=None, help="Folder-safe source download ID.")
    full.add_argument("--download-dir", default=None, help="Override the raw download directory.")
    full.add_argument(
        "--reuse-downloads",
        "--offline",
        dest="reuse_downloads",
        action="store_true",
        help=(
            "Make no remote source, soil, or WBD-service requests; reuse the populated "
            "--download-dir and existing site soil products."
        ),
    )
    full.add_argument(
        "--max-tiles", type=int, default=None, help="Cap files per product; 0 means no cap."
    )
    full.add_argument(
        "--max-file-size-mb",
        type=float,
        default=512.0,
        help="Maximum single download size in MiB; 0 disables the size guard.",
    )
    full.add_argument("--soil-pixel-size", type=float, default=0.0003)
    full.add_argument("--soil-top-depth", type=float, default=30.0)
    full.add_argument(
        "--acquisition-area",
        default=None,
        help="EPSG:4326 GeoJSON area used to size downloads and clip materialized DEM/hydrography.",
    )
    full.add_argument(
        "--use-reviewed-pour-points",
        action="store_true",
        help="Require an existing promoted pour_points.shp and prevent automatic replacement.",
    )
    full.add_argument(
        "--nhdplus-snap-distance-m",
        type=float,
        default=50.0,
        help="Maximum outlet movement for NHDPlus and DEM routing snaps (default: 50 m).",
    )
    full.add_argument("--minimum-watershed-area-km2", type=float, default=0.05)
    full.add_argument("--minimum-subwatershed-area-km2", type=float, default=0.0005)
    full.add_argument("--minimum-area-ratio", type=float, default=0.75)
    full.add_argument("--maximum-area-ratio", type=float, default=1.25)
    full.add_argument(
        "--use-existing-outlet",
        "--preserve-existing-outlet",
        dest="use_existing_outlet",
        action="store_true",
        help="Use outputs/outlet.shp as reviewed input and do not recreate it from --lon/--lat.",
    )
    full.add_argument("--documented-watershed-source", default=None)
    full.add_argument("--documented-watershed-layer", default=None)
    full.add_argument("--documented-watershed-name-field", default=None)
    full.add_argument("--documented-watershed-name", default=None)
    full.add_argument("--documented-watershed-title", default=None)
    full.add_argument("--documented-watershed-organization", default=None)
    full.add_argument("--documented-watershed-url", default=None)
    full.add_argument("--documented-watershed-license", default=None)
    full.add_argument("--documented-watershed-allow-outlet-outside", action="store_true")

    capture_baseline = sub.add_parser(
        "capture-report-baseline",
        help="Capture stable workflow-report fields for later regression checks.",
    )
    capture_baseline.add_argument("--outputs", required=True)
    capture_baseline.add_argument("--out", required=True)

    check_baseline = sub.add_parser(
        "check-report-baseline",
        help="Compare current workflow reports against a captured baseline.",
    )
    check_baseline.add_argument("--outputs", required=True)
    check_baseline.add_argument("--baseline", required=True)
    check_baseline.add_argument("--absolute-tolerance", type=float, default=1e-6)
    check_baseline.add_argument("--relative-tolerance", type=float, default=0.0)
    check_baseline.add_argument("--json", action="store_true")

    sub.add_parser("ui", help="Launch the lightweight GIStoOHQ DEM workflow UI.")

    doctor = sub.add_parser("doctor", help="Check runtime, GIS, and legacy-script availability.")
    doctor.add_argument("--script-dir", default=None)
    doctor.add_argument("--strict-gis", action="store_true")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    return p


def _print_input_result(result) -> None:
    for warning in result.warnings:
        print("WARNING:", warning)
    for error in result.errors:
        print("ERROR:", error)


def _documented_watershed_defaults(config_path: str | None) -> dict[str, str | bool | None]:
    if not config_path:
        return {}
    path = Path(config_path).expanduser().resolve()
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise FullRunError(f"Could not load full-run config {path}: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    reference = data.get("documented_watershed")
    outlet = data.get("outlet") if isinstance(data.get("outlet"), dict) else {}
    if not isinstance(reference, dict):
        reference = {}
    source = reference.get("source")
    if isinstance(source, str) and source:
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source = str(path.parent / source_path)
    outlet_source = (
        outlet.get("source") or outlet.get("kmz") or outlet.get("kml")
        if isinstance(outlet, dict)
        else None
    )
    if isinstance(outlet_source, str) and outlet_source:
        outlet_source_path = Path(outlet_source).expanduser()
        if not outlet_source_path.is_absolute():
            outlet_source = str(path.parent / outlet_source_path)
    documented_snapped_path = (
        outlet.get("documented_snapped_path") or outlet.get("boundary_snapped_path")
        if isinstance(outlet, dict)
        else None
    )
    if isinstance(documented_snapped_path, str) and documented_snapped_path:
        snapped_path = Path(documented_snapped_path).expanduser()
        if not snapped_path.is_absolute():
            documented_snapped_path = str(path.parent / snapped_path)
    return {
        "outlet_source": outlet_source if isinstance(outlet_source, str) else None,
        "snap_outlet_to_documented_watershed": bool(
            outlet.get("snap_to_documented_watershed", False)
        ) if isinstance(outlet, dict) else False,
        "documented_snapped_outlet_path": documented_snapped_path
        if isinstance(documented_snapped_path, str)
        else None,
        "source": source if isinstance(source, str) else None,
        "layer": reference.get("layer") if isinstance(reference.get("layer"), str) else None,
        "name_field": reference.get("name_field")
        if isinstance(reference.get("name_field"), str)
        else None,
        "name": reference.get("name") if isinstance(reference.get("name"), str) else None,
        "title": reference.get("title") if isinstance(reference.get("title"), str) else None,
        "organization": reference.get("organization")
        if isinstance(reference.get("organization"), str)
        else None,
        "url": reference.get("url") if isinstance(reference.get("url"), str) else None,
        "license": reference.get("license") if isinstance(reference.get("license"), str) else None,
        "allow_outlet_outside": bool(reference.get("allow_outlet_outside", False)),
    }


def _validate_inputs(settings: BuilderSettings, no_schema: bool, json_output: bool = False) -> int:
    result = InputValidator().validate(settings, check_schema=not no_schema)
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        _print_input_result(result)
        if result.ok:
            print("Input validation OK")
    return 0 if result.ok else 2


def _maybe_validate_inputs(
    settings: BuilderSettings, skip_input_check: bool, no_schema: bool
) -> int:
    if skip_input_check:
        return 0
    return _validate_inputs(settings, no_schema)


def _legacy_options_from_args(args) -> LegacyWorkflowOptions:
    return LegacyWorkflowOptions(
        out_dir=getattr(args, "out_dir", None),
        dem_path=getattr(args, "dem_path", None),
        outlet_path=getattr(args, "outlet_path", None),
        flowline_path=getattr(args, "flowline_path", None),
        flowdir_path=getattr(args, "flowdir_path", None),
        flowacc_path=getattr(args, "flowacc_path", None),
        target_epsg=getattr(args, "target_epsg", None),
        force=not getattr(args, "no_force", False),
        dry_run=getattr(args, "dry_run", False),
        auto_pour_points=not getattr(args, "no_auto_pour_points", False),
        auto_outlet=not getattr(args, "no_auto_outlet", False),
        start_at=getattr(args, "start_at", None),
    )


def _load_cli_config(config_path: str) -> dict:
    path = Path(config_path).expanduser()
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _cli_site_name(config: dict) -> str:
    site = config.get("site")
    if isinstance(site, dict):
        return str(site.get("name") or ".")
    return str(site or ".")


def _cli_target_crs(config: dict) -> str | None:
    site = config.get("site")
    if isinstance(site, dict) and site.get("target_crs") and site.get("target_crs") != "auto":
        return str(site["target_crs"])
    value = config.get("target_crs")
    return str(value) if value and value != "auto" else None


def _resolve_cli_path(config_path: str, value, default: str | None = None) -> str | None:
    raw = value or default
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return str(path)
    return str(Path(config_path).expanduser().parent / path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "data":
        try:
            if args.data_command == "init-site":
                result = write_site_spec(
                    args.site_spec,
                    site_id=args.site_id,
                    name=args.name,
                    longitude=args.lon,
                    latitude=args.lat,
                    start=args.start,
                    end=args.end,
                    overwrite=args.force,
                )
                print(f"Wrote watershed SiteSpec: {result}")
            elif args.data_command == "validate-site":
                spec = SiteSpec.from_file(args.site_spec)
                print(f"SiteSpec valid: {spec.site_id} ({spec.digest})")
            elif args.data_command == "acquire-url":
                asset = acquire_url(
                    url=args.url,
                    provider=args.provider,
                    product=args.product,
                    product_version=args.product_version,
                    cache=args.cache,
                    catalog=args.catalog,
                )
                print(f"Cataloged asset: {asset['asset_id']}")
            elif args.data_command == "freeze":
                manifest = freeze_package(
                    site_spec=args.site_spec, catalog=args.catalog, output=args.output,
                    include_raw=args.include_raw, object_store=args.object_store,
                    redistributable=args.redistributable,
                )
                print(f"Froze watershed package: {manifest}")
            elif args.data_command == "validate-package":
                manifest = validate_package(args.package)
                print(f"Watershed package valid: {manifest.package_id}")
            elif args.data_command == "reconnaissance":
                report = run_reconnaissance(
                    args.site_spec, args.output, radius_km=args.radius_km
                )
                print(f"Gauge reconnaissance: {report['decision']} ({len(report['candidates'])} candidates)")
            elif args.data_command == "download-discharge":
                spec = SiteSpec.from_file(args.site_spec)
                asset = acquire_observed_discharge(
                    spec, args.station_id, cache=args.cache, catalog=args.catalog
                )
                print(
                    f"Cataloged native discharge: {asset['asset_id']} "
                    f"({asset['observation_count']} observations)"
                )
            elif args.data_command == "download-weather":
                spec = SiteSpec.from_file(args.site_spec)
                variables = tuple(value.strip() for value in args.variables.split(",") if value.strip())
                asset = acquire_historical_meteorology(
                    spec, cache=args.cache, catalog=args.catalog, parameters=variables
                )
                print(f"Cataloged native meteorology: {asset['asset_id']} ({len(variables)} variables)")
            elif args.data_command == "harmonize":
                asset = harmonize_asset(
                    asset_id=args.asset_id, catalog=args.catalog, object_store=args.object_store,
                    qc_output=args.qc_output, provenance_output=args.provenance_output,
                )
                print(f"Cataloged harmonized temporal asset: {asset['asset_id']}")
            elif args.data_command == "download-pet":
                spec = SiteSpec.from_file(args.site_spec)
                variables = tuple(value.strip() for value in args.variables.split(",") if value.strip())
                asset = acquire_pet_et(
                    spec, cache=args.cache, catalog=args.catalog, parameters=variables
                )
                print(f"Cataloged native PET/ET: {asset['asset_id']}")
            elif args.data_command == "export-hydropinn":
                manifest = export_hydropinn(
                    package=args.package, object_store=args.object_store,
                    output=args.output, profile=args.profile,
                )
                print(f"Exported HydroPINN profile: {manifest}")
            elif args.data_command == "run":
                result = run_watershed_data_pipeline(
                    site_spec=args.site_spec, station_id=args.station_id,
                    workspace=args.workspace, include_discharge=not args.no_discharge,
                    include_weather=not args.no_weather, include_pet=not args.no_pet,
                    export_hydropinn_profile=args.export_hydropinn,
                )
                print(f"Watershed data pipeline complete: {result['package_manifest']}")
            elif args.data_command == "download-forecast":
                asset = acquire_forecast_archive(
                    url=args.url, provider=args.provider, product=args.product,
                    cache=args.cache, catalog=args.catalog,
                )
                print(f"Cataloged forecast archive: {asset['asset_id']}")
            elif args.data_command == "forecast-view":
                asset = materialize_available_forecasts(
                    asset_id=args.asset_id, prediction_time=args.prediction_time,
                    object_store=args.object_store, catalog=args.catalog,
                )
                print(f"Cataloged leakage-safe forecast view: {asset['asset_id']}")
            elif args.data_command == "status":
                report = write_data_status(
                    catalog=args.catalog, object_store=args.object_store, output=args.output
                )
                print(f"Wrote watershed data status: {report}")
        except WatershedDataError as exc:
            print(f"data {args.data_command} failed: {exc}")
            return 2
        return 0
    if args.command == "build":
        settings = BuilderSettings.from_args(args.root, args.site, args.config, args.project_name)
        input_status = _maybe_validate_inputs(settings, args.skip_input_check, args.no_schema)
        if input_status != 0:
            return input_status
        out = Path(args.out).expanduser().resolve() if args.out else None
        result = build_ohq_project(settings, output_path=out, dry_run=args.dry_run)
        if result:
            print(result)
        return 0
    if args.command == "build-hms":
        settings = BuilderSettings.from_args(args.root, args.site, args.config, args.project_name)
        try:
            result = build_hms_project(settings, args.out_dir)
        except Exception as exc:
            print(f"build-hms failed: {exc}")
            return 2
        print(f"Wrote HEC-HMS project: {result.project_file}")
        return 0
    if args.command == "validate-hms":
        try:
            references = validate_hms_project(args.project)
        except (OSError, ValueError) as exc:
            print(f"validate-hms failed: {exc}")
            return 2
        print(f"HEC-HMS project references OK: {len(references)} file(s)")
        return 0
    if args.command == "validate":
        settings = BuilderSettings.from_args(args.root, args.site, args.config)
        input_status = _maybe_validate_inputs(settings, args.skip_input_check, args.no_schema)
        if input_status != 0:
            return input_status
        build_ohq_project(settings, dry_run=True)
        return 0
    if args.command == "prepare-inputs":
        try:
            run_legacy_input_workflow(
                args.root,
                args.site,
                args.script_dir,
                args.phase,
                _legacy_options_from_args(args),
            )
        except LegacyInputWorkflowError as exc:
            print(f"prepare-inputs failed: {exc}")
            return 2
        return 0
    if args.command == "prepare-hydrology":
        try:
            run_hydrology_preprocessing(
                args.root,
                args.site,
                args.script_dir,
                _legacy_options_from_args(args),
            )
        except LegacyInputWorkflowError as exc:
            print(f"prepare-hydrology failed: {exc}")
            return 2
        print("Hydrology preprocessing complete.")
        return 0
    if args.command == "create-pour-points":
        site_path = Path(args.site).expanduser()
        if not site_path.is_absolute():
            site_path = Path(args.root).expanduser().resolve() / site_path
        outputs = site_path.resolve() / "outputs"
        junctions = (
            Path(args.junctions).expanduser() if args.junctions else outputs / "junctions.gpkg"
        )
        output = Path(args.out).expanduser() if args.out else outputs / "pour_points.shp"
        fallback_outlet = (
            Path(args.fallback_outlet).expanduser()
            if args.fallback_outlet
            else outputs / "outlet.shp"
        )
        try:
            result = generate_pour_points(
                junctions,
                output,
                flow_accumulation_path=(Path(args.flow_acc).expanduser() if args.flow_acc else outputs / "flow_acc.tif"),
                fallback_outlet_path=fallback_outlet,
                overwrite=args.overwrite,
            )
        except PourPointGenerationError as exc:
            print(f"create-pour-points failed: {exc}")
            return 2
        print(f"Generated {result.count} pour point(s): {result.output_path}")
        print(f"Pour-point generation report: {result.report_path}")
        return 0
    if args.command == "promote-pour-points":
        site_path = Path(args.site).expanduser()
        if not site_path.is_absolute():
            site_path = Path(args.root).expanduser().resolve() / site_path
        outputs = site_path.resolve() / "outputs"
        try:
            promoted = promote_pour_point_candidates(
                args.candidates or outputs / "pour_point_candidates.gpkg",
                args.boundary or outputs / "watershed_boundary.gpkg",
                args.out or outputs / "pour_points.shp",
                minimum_spacing_m=args.minimum_spacing_m,
                overwrite=args.overwrite,
            )
        except PourPointCandidateError as exc:
            print(f"promote-pour-points failed: {exc}")
            return 2
        print(f"Wrote approved Phase 2 pour points: {promoted}")
        return 0
    if args.command == "import-watershed-reference":
        site_path = Path(args.site).expanduser()
        if not site_path.is_absolute():
            site_path = Path(args.root).expanduser().resolve() / site_path
        target = (
            Path(args.out).expanduser()
            if args.out
            else site_path.resolve() / "outputs" / REFERENCE_FILENAME
        )
        try:
            imported = import_documented_watershed(
                args.source,
                target,
                outlet_lon=args.lon,
                outlet_lat=args.lat,
                layer=args.layer,
                name_field=args.name_field,
                name=args.name,
                where=args.where,
                source_title=args.source_title,
                source_organization=args.source_organization,
                source_url=args.source_url,
                license_text=args.license_text,
                require_outlet_containment=not args.allow_outlet_outside,
            )
        except DocumentedWatershedError as exc:
            print(f"import-watershed-reference failed: {exc}")
            return 2
        print(f"Wrote documented watershed reference: {imported}")
        print(f"Wrote provenance metadata: {imported.with_suffix('.json')}")
        return 0
    if args.command == "export-watershed-coordinates":
        try:
            exported = export_boundary_vertices(
                args.source,
                args.out,
                layer=args.layer,
                target_crs=args.target_crs,
            )
        except DocumentedWatershedError as exc:
            print(f"export-watershed-coordinates failed: {exc}")
            return 2
        print(f"Wrote watershed boundary coordinates: {exported}")
        return 0
    if args.command == "create-outlet":
        site_path = Path(args.site).expanduser()
        if not site_path.is_absolute():
            site_path = Path(args.root).expanduser().resolve() / site_path
        outputs = site_path.resolve() / "outputs"
        flow_acc = Path(args.flow_acc).expanduser() if args.flow_acc else outputs / "flow_acc.tif"
        output = Path(args.out).expanduser() if args.out else outputs / "outlet.shp"
        try:
            result = create_outlet_from_flow_accumulation(
                flow_acc, output, overwrite=args.overwrite
            )
        except OutletCreationError as exc:
            print(f"create-outlet failed: {exc}")
            return 2
        print(
            f"Created outlet at ({result.x:.3f}, {result.y:.3f}), "
            f"flow accumulation {result.accumulation:g}: {result.output_path}"
        )
        return 0
    if args.command == "full-run":
        try:
            if args.config:
                config_path = Path(args.config).expanduser().resolve()
                config_data = _load_cli_config(str(config_path))
                dem_section = config_data.get("dem_acquisition")
                if (
                    isinstance(dem_section, dict)
                    and dem_section.get("method")
                    and dem_section.get("acquisition_area")
                ):
                    refreshed = prepare_dem_from_config(config_path)
                    if not args.acquisition_area:
                        acquisition_path = Path(str(dem_section["acquisition_area"])).expanduser()
                        if not acquisition_path.is_absolute():
                            acquisition_path = config_path.parent / acquisition_path
                        args.acquisition_area = str(acquisition_path.resolve())
                    print(
                        "Refreshed config-driven outlet/acquisition inputs before full-run: "
                        f"{refreshed.summary_path}"
                    )
            reference_defaults = _documented_watershed_defaults(args.config)
            documented_source = (
                args.documented_watershed_source or reference_defaults.get("source")
            )
            allow_outlet_outside = (
                args.documented_watershed_allow_outlet_outside
                or bool(reference_defaults.get("allow_outlet_outside"))
            )
            outlet_source = args.outlet_source or reference_defaults.get("outlet_source")
            if not outlet_source and (args.lon is None or args.lat is None):
                raise FullRunError("full-run requires --outlet-source or both --lon and --lat.")
            result = run_full_pipeline(
                args.root,
                args.site,
                lon=args.lon,
                lat=args.lat,
                project_name=args.project_name,
                output_path=args.out,
                script_dir=args.script_dir,
                buffer_m=args.buffer,
                target_crs=args.target_crs,
                site_id=args.site_id,
                download_dir=args.download_dir,
                max_tiles=args.max_tiles,
                max_file_size_mb=args.max_file_size_mb,
                soil_pixel_size=args.soil_pixel_size,
                soil_top_depth=args.soil_top_depth,
                acquisition_area=args.acquisition_area,
                use_reviewed_pour_points=args.use_reviewed_pour_points,
                nhdplus_snap_distance_m=args.nhdplus_snap_distance_m,
                minimum_watershed_area_km2=args.minimum_watershed_area_km2,
                minimum_subwatershed_area_km2=args.minimum_subwatershed_area_km2,
                minimum_area_ratio=args.minimum_area_ratio,
                maximum_area_ratio=args.maximum_area_ratio,
                use_existing_outlet=args.use_existing_outlet,
                reuse_downloads=args.reuse_downloads,
                outlet_source=outlet_source,
                snap_outlet_to_documented_watershed=(
                    args.snap_outlet_to_documented_watershed
                    or bool(reference_defaults.get("snap_outlet_to_documented_watershed"))
                ),
                documented_snapped_outlet_path=reference_defaults.get(
                    "documented_snapped_outlet_path"
                ),
                documented_watershed_source=documented_source,
                documented_watershed_layer=(
                    args.documented_watershed_layer or reference_defaults.get("layer")
                ),
                documented_watershed_name_field=(
                    args.documented_watershed_name_field
                    or reference_defaults.get("name_field")
                ),
                documented_watershed_name=(
                    args.documented_watershed_name or reference_defaults.get("name")
                ),
                documented_watershed_title=(
                    args.documented_watershed_title or reference_defaults.get("title")
                ),
                documented_watershed_organization=(
                    args.documented_watershed_organization
                    or reference_defaults.get("organization")
                ),
                documented_watershed_url=(
                    args.documented_watershed_url or reference_defaults.get("url")
                ),
                documented_watershed_license=(
                    args.documented_watershed_license or reference_defaults.get("license")
                ),
                documented_watershed_allow_outlet_outside=allow_outlet_outside,
                progress=lambda message: print(message, flush=True),
            )
        except FullRunError as exc:
            print(f"full-run failed: {exc}")
            return 2
        print(f"Full pipeline complete: {result.output_path}")
        if result.hms_project_path:
            print(f"HEC-HMS project complete: {result.hms_project_path}")
        if result.report_path:
            print(f"Watershed report complete: {result.report_path}")
        return 0
    if args.command == "download-data":
        try:
            default_output = str(
                Path(args.input_csv).with_name(Path(args.input_csv).stem + "_dem.csv")
            )
            results = process_csv(
                args.input_csv,
                args.output_csv or default_output,
                products=parse_products(args.products),
                download_dir=args.download,
                id_col=args.id_col,
                lat_col=args.lat_col,
                lon_col=args.lon_col,
                buffer_m=args.buffer,
                max_tiles=args.max_tiles,
                max_file_size_mb=args.max_file_size_mb,
                dem_resolution=args.dem_resolution,
                make_points=args.make_points,
                points_dir=args.points_dir,
                tiger_year=args.tiger_year,
                nlcd_year=args.nlcd_year,
                progress=lambda message: print(message, flush=True),
            )
        except Exception as exc:  # pragma: no cover - CLI boundary
            print(f"download-data failed: {exc}")
            return 2
        for result in results:
            print(
                f"{result.site_id} {result.product}: {result.status}; "
                f"{result.count} item(s), downloaded {result.downloaded}"
            )
        return 0
    if args.command == "download-inputs":
        try:
            result = download_all_inputs(
                args.root,
                args.site,
                lon=args.lon,
                lat=args.lat,
                site_id=args.site_id,
                download_dir=args.download_dir,
                buffer_m=args.buffer,
                max_tiles=args.max_tiles,
                max_file_size_mb=args.max_file_size_mb,
                soil_pixel_size=args.soil_pixel_size,
                soil_top_depth=args.soil_top_depth,
                progress=lambda message: print(message, flush=True),
            )
        except Exception as exc:  # pragma: no cover - CLI boundary
            print(f"download-inputs failed: {exc}")
            return 2
        print(f"Downloaded DEM/hydrography under: {result.download_dir}")
        print(f"Wrote HSG data: {result.hsg.vector_path}")
        print(f"Wrote soil texture data: {result.texture.vector_path}")
        return 0
    if args.command == "download-dem-manifest":
        try:
            result = download_dem_manifest(
                args.manifest,
                args.out_dir,
                updated_manifest_path=args.updated_manifest,
            )
        except Exception as exc:  # pragma: no cover - CLI boundary
            print(f"download-dem-manifest failed: {exc}")
            return 2
        print(f"Wrote DEM manifest: {result.manifest_path}")
        print(f"Downloaded tile count: {result.downloaded}")
        print(f"Skipped existing tile count: {result.skipped}")
        print(f"Materialized tile count: {result.tile_count}")
        return 0
    if args.command == "download-hsg":
        try:
            result = retrieve_hydrologic_soil_groups(
                args.root, args.site, buffer=args.buffer, pixel_size=args.pixel_size
            )
        except SoilRetrievalError as exc:
            print(f"download-hsg failed: {exc}")
            return 2
        print(f"Wrote HSG vector: {result.vector_path}")
        for raster in result.raster_paths:
            print(f"Wrote HSG raster: {raster}")
        return 0
    if args.command == "materialize-inputs":
        try:
            result = materialize_source_inputs(
                args.root,
                args.site,
                source_dir=args.source_dir,
                target_crs=args.target_crs,
                clip_bounds=args.clip_bounds,
                clip_bounds_crs=args.clip_bounds_crs,
                clip_center_lon=args.clip_center_lon,
                clip_center_lat=args.clip_center_lat,
                clip_buffer_m=args.clip_buffer,
                clip_buffer_scale=args.clip_buffer_scale,
                dem_manifest=args.dem_manifest,
            )
        except Exception as exc:  # pragma: no cover - CLI boundary
            print(f"materialize-inputs failed: {exc}")
            return 2
        print(f"Wrote DEM: {result.dem.output_path}")
        print(f"Wrote flowlines: {result.hydro.output_path}")
        catchment_path = getattr(result.hydro, "catchment_path", None)
        if catchment_path is not None:
            print(f"Wrote NHDPlus catchments: {catchment_path}")
        landcover = getattr(result, "landcover", None)
        if landcover is not None:
            print(f"Wrote landcover: {landcover}")
        cn_lookup = getattr(result, "cn_lookup", None)
        if cn_lookup is not None:
            print(f"Wrote CN lookup: {cn_lookup}")
        wbd_reference = getattr(result, "wbd_reference", None)
        if wbd_reference is not None:
            print(f"Wrote WBD HUC12 reference: {wbd_reference}")
        return 0

    if args.command == "init-dem-config":
        try:
            path = write_dem_config_template(
                args.config,
                site=args.site,
                lon=args.lon,
                lat=args.lat,
                flowline_path=args.flowlines,
                tile_index=args.tile_index,
                target_crs=args.target_crs,
                method=args.method,
                overwrite=args.force,
                use_demo_inputs=args.demo_inputs,
            )
        except (DemWorkflowError, ValueError) as exc:
            print(f"init-dem-config failed: {exc}")
            return 2
        print(f"Wrote DEM config: {path}")
        print("Next: ohqbuild prepare-dem --config " + str(path))
        return 0

    if args.command == "run-dem-prep":
        try:
            result = prepare_dem_from_config(args.config)
            print(f"Wrote DEM workflow summary: {result.summary_path}")
            if result.acquisition_area:
                print(f"Wrote acquisition area: {result.acquisition_area.output_path}")
            manifest_path = result.tile_manifest.output_path if result.tile_manifest else None
            if result.tile_manifest:
                print(f"Wrote tile manifest: {manifest_path}")
                print(f"Selected tile count: {result.tile_manifest.selected_count}")
            config = _load_cli_config(args.config)
            dem = (
                config.get("dem_acquisition", {})
                if isinstance(config.get("dem_acquisition"), dict)
                else {}
            )
            paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
            if args.download:
                if manifest_path is None:
                    raise DemWorkflowError(
                        "run-dem-prep --download requires a tile manifest from prepare-dem."
                    )
                raw_dem_dir = _resolve_cli_path(
                    args.config, paths.get("raw_dem_dir") or dem.get("raw_dem_dir"), "dem/raw"
                )
                download_result = download_dem_manifest(manifest_path, raw_dem_dir)
                print(f"Downloaded tile count: {download_result.downloaded}")
                print(f"Skipped existing tile count: {download_result.skipped}")
            if args.materialize:
                if manifest_path is None:
                    raise DemWorkflowError(
                        "run-dem-prep --materialize requires a tile manifest from prepare-dem."
                    )
                materialized = materialize_source_inputs(
                    _resolve_cli_path(args.config, config.get("root"), "."),
                    _cli_site_name(config),
                    source_dir=_resolve_cli_path(
                        args.config, config.get("download_dir") or config.get("source_dir")
                    ),
                    target_crs=_cli_target_crs(config),
                    dem_manifest=str(manifest_path),
                )
                print(f"Wrote DEM: {materialized.dem.output_path}")
                print(f"Wrote flowlines: {materialized.hydro.output_path}")
            if args.validate:
                validation = validate_dem_from_config(args.config)
                print(f"Wrote DEM validation summary: {validation.summary_path}")
                print(f"Boundary validation: {'OK' if validation.is_valid else 'EXPAND'}")
                print(
                    f"Touched edges: {','.join(validation.touched_edges) if validation.touched_edges else 'none'}"
                )
                if validation.expanded_area:
                    print(
                        f"Wrote expanded acquisition area: {validation.expanded_area.output_path}"
                    )
        except Exception as exc:  # pragma: no cover - CLI boundary
            print(f"run-dem-prep failed: {exc}")
            return 2
        return 0
    if args.command == "prepare-dem":
        try:
            result = prepare_dem_from_config(args.config)
        except (DemWorkflowError, DemAcquisitionError, ValueError) as exc:
            print(f"prepare-dem failed: {exc}")
            return 2
        print(f"Wrote DEM workflow summary: {result.summary_path}")
        if result.acquisition_area:
            print(f"Wrote acquisition area: {result.acquisition_area.output_path}")
        if result.tile_manifest:
            print(f"Wrote tile manifest: {result.tile_manifest.output_path}")
            print(f"Selected tile count: {result.tile_manifest.selected_count}")
        return 0
    if args.command == "validate-dem":
        try:
            result = validate_dem_from_config(args.config)
        except (DemWorkflowError, DemAcquisitionError, ValueError) as exc:
            print(f"validate-dem failed: {exc}")
            return 2
        print(f"Wrote DEM validation summary: {result.summary_path}")
        print(f"Boundary validation: {'OK' if result.is_valid else 'EXPAND'}")
        print(
            f"Touched edges: {','.join(result.touched_edges) if result.touched_edges else 'none'}"
        )
        if result.expanded_area:
            print(f"Wrote expanded acquisition area: {result.expanded_area.output_path}")
        return 0 if result.is_valid else 3
    if args.command == "watershed-bounds":
        try:
            result = resolve_materialization_bounds(
                lon=args.lon,
                lat=args.lat,
                buffer_m=args.buffer,
                safety_scale=args.safety_scale,
                prefer_web=not args.no_web,
                timeout=args.timeout,
            )
        except WatershedBoundsError as exc:
            print(f"watershed-bounds failed: {exc}")
            return 2
        minx, miny, maxx, maxy = result.bounds
        if args.json:
            print(json.dumps({"bounds": result.bounds, "source": result.source, "url": result.url}))
        else:
            print(f"{minx},{miny},{maxx},{maxy}")
        return 0
    if args.command == "download-texture":
        try:
            result = retrieve_soil_texture(
                args.root,
                args.site,
                buffer=args.buffer,
                pixel_size=args.pixel_size,
                top_depth=args.top_depth,
            )
        except SoilRetrievalError as exc:
            print(f"download-texture failed: {exc}")
            return 2
        print(f"Wrote texture vector: {result.vector_path}")
        for raster in result.raster_paths:
            print(f"Wrote texture raster: {raster}")
        return 0
    if args.command == "materialize-dem":
        try:
            result = materialize_dem(
                args.root,
                args.site,
                source_dir=args.source_dir,
                output_path=args.out,
                dst_crs=args.dst_crs,
                manifest_path=args.manifest,
            )
        except DemMaterializeError as exc:
            print(f"materialize-dem failed: {exc}")
            return 2
        print(f"Wrote DEM: {result.output_path}")
        print(f"Source product count: {result.source_count}")
        print(f"Target CRS: {result.dst_crs}")
        return 0
    if args.command == "dem-acquisition-area":
        try:
            result = create_outlet_buffer_area(
                args.lon,
                args.lat,
                args.out,
                upstream_km=args.upstream_km,
                downstream_km=args.downstream_km,
                lateral_km=args.lateral_km,
                azimuth_deg=args.azimuth,
            )
        except DemAcquisitionError as exc:
            print(f"dem-acquisition-area failed: {exc}")
            return 2
        minx, miny, maxx, maxy = result.bounds
        print(f"Wrote acquisition area: {result.output_path}")
        print(f"Mode: {result.mode}")
        print(f"Area: {result.area_km2:g} km^2")
        print(f"Bounds: {minx},{miny},{maxx},{maxy}")
        return 0

    if args.command == "dem-snap-outlet":
        try:
            result = snap_outlet_to_flowlines(
                args.lon,
                args.lat,
                args.flowlines,
                snap_distance_m=args.snap_distance_m,
                output_path=args.out,
            )
        except DemAcquisitionError as exc:
            print(f"dem-snap-outlet failed: {exc}")
            return 2
        print(f"Wrote snapped outlet: {result.output_path}")
        print(f"Snapped outlet: {result.snapped_lon},{result.snapped_lat}")
        print(f"Snap distance: {result.distance_m:g} m")
        return 0
    if args.command == "dem-upstream-network-area":
        try:
            result = create_upstream_network_area(
                args.lon,
                args.lat,
                args.flowlines,
                args.out,
                upstream_trace_distance_km=args.upstream_trace_km,
                upstream_margin_km=args.upstream_margin_km,
                downstream_margin_km=args.downstream_margin_km,
                lateral_margin_km=args.lateral_margin_km,
                envelope_type=args.envelope_type,
            )
        except DemAcquisitionError as exc:
            print(f"dem-upstream-network-area failed: {exc}")
            return 2
        minx, miny, maxx, maxy = result.bounds
        print(f"Wrote acquisition area: {result.output_path}")
        print(f"Mode: {result.mode}")
        print(f"Area: {result.area_km2:g} km^2")
        print(f"Bounds: {minx},{miny},{maxx},{maxy}")
        return 0
    if args.command == "dem-tile-manifest":
        try:
            result = build_dem_tile_manifest(
                args.acquisition_area,
                args.tile_index,
                args.out,
                url_field=args.url_field,
                path_field=args.path_field,
            )
        except DemAcquisitionError as exc:
            print(f"dem-tile-manifest failed: {exc}")
            return 2
        print(f"Wrote DEM tile manifest: {result.output_path}")
        print(f"Selected tile count: {result.selected_count}")
        minx, miny, maxx, maxy = result.acquisition_bounds
        print(f"Acquisition bounds: {minx},{miny},{maxx},{maxy}")
        return 0
    if args.command == "dem-boundary-check":
        try:
            result = validate_watershed_within_acquisition(
                args.watershed,
                args.acquisition_area,
                safety_distance_m=args.safety_distance_m,
            )
        except DemAcquisitionError as exc:
            print(f"dem-boundary-check failed: {exc}")
            return 2
        if args.json:
            print(
                json.dumps(
                    {
                        "is_valid": result.is_valid,
                        "touched_edges": result.touched_edges,
                        "distances_m": result.distances_m,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Boundary validation: {'OK' if result.is_valid else 'EXPAND'}")
            print(
                f"Touched edges: {','.join(result.touched_edges) if result.touched_edges else 'none'}"
            )
            for edge, distance in result.distances_m.items():
                print(f"Distance {edge}: {distance:g} m")
        return 0 if result.is_valid else 3
    if args.command == "dem-expand-area":
        try:
            result = expand_acquisition_bounds(
                args.acquisition_area,
                args.out,
                tuple(edge.strip() for edge in args.edges.split(",") if edge.strip()),
                expansion_distance_km=args.expansion_distance_km,
            )
        except DemAcquisitionError as exc:
            print(f"dem-expand-area failed: {exc}")
            return 2
        minx, miny, maxx, maxy = result.bounds
        print(f"Wrote expanded acquisition area: {result.output_path}")
        print(f"Bounds: {minx},{miny},{maxx},{maxy}")
        return 0
    if args.command == "fetch-phase1-inputs":
        try:
            result = fetch_phase1_inputs(
                args.root,
                args.site,
                lon=args.lon,
                lat=args.lat,
                site_id=args.site_id,
                products=args.products,
                download_dir=args.download_dir,
                buffer_m=args.buffer,
                max_tiles=args.max_tiles,
                max_file_size_mb=args.max_file_size_mb,
                skip_outlet=args.skip_outlet,
            )
        except (Phase1FetchError, ValueError) as exc:
            print(f"fetch-phase1-inputs failed: {exc}")
            return 2
        if result.outlet_path:
            print(f"Created outlet: {result.outlet_path}")
        print(f"Downloaded source data under: {result.download_dir}")
        print(f"Wrote summary: {result.summary_csv}")
        print(f"Wrote manifest: {result.manifest_path}")
        return 0
    if args.command == "init-inputs":
        manifest = write_input_manifest(args.root, args.site)
        print(f"Created input folders and checklist: {manifest}")
        return 0
    if args.command == "check-inputs":
        settings = BuilderSettings.from_args(args.root, args.site, args.config)
        return _validate_inputs(settings, args.no_schema, args.json)
    if args.command == "run":
        if not args.skip_prepare:
            try:
                legacy_options = _legacy_options_from_args(args)
                legacy_options = LegacyWorkflowOptions(
                    **{**legacy_options.__dict__, "dry_run": args.prepare_dry_run}
                )
                run_legacy_input_workflow(
                    args.root,
                    args.site,
                    args.script_dir,
                    args.phase,
                    legacy_options,
                )
            except LegacyInputWorkflowError as exc:
                print(f"prepare-inputs failed: {exc}")
                return 2
        settings = BuilderSettings.from_args(args.root, args.site, args.config, args.project_name)
        input_status = _validate_inputs(settings, args.no_schema)
        if input_status != 0:
            return input_status
        out = Path(args.out).expanduser().resolve() if args.out else None
        result = build_ohq_project(settings, output_path=out)
        if result:
            print(result)
        return 0
    if args.command == "capture-report-baseline":
        try:
            result = create_report_baseline(args.outputs, args.out)
        except ReportBaselineError as exc:
            print(f"capture-report-baseline failed: {exc}")
            return 2
        print(f"Wrote workflow report baseline: {result}")
        return 0
    if args.command == "check-report-baseline":
        try:
            result = compare_report_baseline(
                args.outputs,
                args.baseline,
                absolute_tolerance=args.absolute_tolerance,
                relative_tolerance=args.relative_tolerance,
            )
        except (ReportBaselineError, OSError, json.JSONDecodeError) as exc:
            print(f"check-report-baseline failed: {exc}")
            return 2
        if args.json:
            print(json.dumps({"passed": result.passed, "differences": result.differences}, indent=2))
        elif result.passed:
            print("Workflow reports match the baseline.")
        else:
            print(f"Workflow report regression: {len(result.differences)} difference(s)")
            for difference in result.differences:
                print(
                    f"  {difference['path']}: expected={difference['expected']!r}, "
                    f"actual={difference['actual']!r}"
                )
        return 0 if result.passed else 3
    if args.command == "ui":
        from .ui.launcher import main as launch_ui

        return launch_ui()
    if args.command == "doctor":
        report = run_doctor(args.script_dir, args.strict_gis)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            for line in report.lines():
                print(line)
        return 0 if report.ok else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

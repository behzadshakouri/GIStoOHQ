from __future__ import annotations

import argparse
import json
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
from .dem_workflow import DemWorkflowError, prepare_dem_from_config, validate_dem_from_config, write_dem_config_template
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
from .outlet_creator import OutletCreationError, create_outlet_from_flow_accumulation
from .full_runner import FullRunError, run_full_pipeline
from .input_downloader import download_all_inputs
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .soil_retrieval import SoilRetrievalError, retrieve_hydrologic_soil_groups, retrieve_soil_texture
from .source_materializer import materialize_source_inputs
from .validation.input_validator import InputValidator
from .watershed_bounds import WatershedBoundsError, resolve_materialization_bounds


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ohqbuild", description="Build OpenHydroQual OHQ files from GIS outputs.")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="Build an OHQ file.")
    b.add_argument("--root", required=True)
    b.add_argument("--site", required=True)
    b.add_argument("--config", default=None)
    b.add_argument("--project-name", default=None)
    b.add_argument("--out", default=None)
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--skip-input-check", action="store_true")
    b.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")

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
    prep.add_argument("--out-dir", default=None, help="Legacy outputs directory; defaults to <root>/<site>/outputs.")
    prep.add_argument("--dem-path", default=None, help="Real-elevation DEM path passed to Phase 1 scripts.")
    prep.add_argument("--outlet-path", default=None, help="Outlet shapefile path passed to legacy scripts.")
    prep.add_argument("--flowline-path", default=None, help="Flowline path passed to legacy scripts.")
    prep.add_argument("--flowdir-path", default=None, help="flow_dir.tif path passed to Phase 1 scripts.")
    prep.add_argument("--flowacc-path", default=None, help="flow_acc.tif path passed to Phase 1 scripts.")
    prep.add_argument("--target-epsg", default=None, help="Target EPSG code forwarded to legacy scripts.")
    prep.add_argument("--no-force", action="store_true", help="Forward FORCE=False to legacy scripts.")
    prep.add_argument("--dry-run", action="store_true", help="Run legacy preflight and list steps without executing processing.")
    prep.add_argument("--start-at", default=None, help="Resume a phase at the named legacy step script, e.g. load_cn_inputs.py.")
    prep.add_argument("--no-auto-pour-points", action="store_true", help="Require an existing pour_points.shp instead of generating it from Phase 1 junctions.")
    prep.add_argument("--no-auto-outlet", action="store_true", help="Require an existing outlet.shp instead of deriving it from flow_acc.tif.")

    hydro_prep = sub.add_parser(
        "prepare-hydrology",
        help="Create flow_dir.tif and flow_acc.tif from materialized DEM and hydrography.",
    )
    hydro_prep.add_argument("--root", required=True)
    hydro_prep.add_argument("--site", required=True)
    hydro_prep.add_argument("--script-dir", default=None)
    hydro_prep.add_argument("--out-dir", default=None, help="Legacy outputs directory; defaults to <root>/<site>/outputs.")
    hydro_prep.add_argument("--dem-path", default=None, help="DEM path passed to hydrology preprocessing.")
    hydro_prep.add_argument("--flowline-path", default=None, help="Flowline path passed to hydrology preprocessing.")
    hydro_prep.add_argument("--flowdir-path", default=None, help="flow_dir.tif output path.")
    hydro_prep.add_argument("--flowacc-path", default=None, help="flow_acc.tif output path.")
    hydro_prep.add_argument("--target-epsg", default=None, help="Target EPSG code forwarded to legacy scripts.")
    hydro_prep.add_argument("--no-force", action="store_true", help="Forward FORCE=False to legacy scripts.")
    hydro_prep.add_argument("--dry-run", action="store_true", help="Run preflight and list steps without executing processing.")

    outlet = sub.add_parser(
        "create-outlet",
        help="Create outlet.shp at the maximum flow-accumulation cell.",
    )
    outlet.add_argument("--root", required=True)
    outlet.add_argument("--site", required=True)
    outlet.add_argument("--flow-acc", default=None, help="Defaults to <root>/<site>/outputs/flow_acc.tif.")
    outlet.add_argument("--out", default=None, help="Defaults to <root>/<site>/outputs/outlet.shp.")
    outlet.add_argument("--overwrite", action="store_true")

    pour = sub.add_parser(
        "create-pour-points",
        help="Create Phase 2 pour_points.shp automatically from Phase 1 junctions.",
    )
    pour.add_argument("--root", required=True)
    pour.add_argument("--site", required=True)
    pour.add_argument("--junctions", default=None, help="Defaults to <root>/<site>/outputs/junctions.gpkg.")
    pour.add_argument("--out", default=None, help="Defaults to <root>/<site>/outputs/pour_points.shp.")
    pour.add_argument("--overwrite", action="store_true")

    dl = sub.add_parser(
        "download-data",
        help="Query/download USGS DEM and hydrography products for site coordinates.",
    )
    dl.add_argument("input_csv", help="CSV with WGS84 latitude/longitude columns.")
    dl.add_argument("output_csv", nargs="?", default=None, help="Optional CSV summary to write.")
    dl.add_argument(
        "--products",
        default="dem",
        help="dem/demhr, demlr, hydro, roads, landcover/nlcd, atlas14, all, or a comma-separated subset (default: dem).",
    )
    dl.add_argument("--download", default=None, help="Directory for per-site downloads.")
    dl.add_argument("--id-col", default=None, help="Column used for per-site folder names.")
    dl.add_argument("--lat-col", default=None, help="Latitude column (auto-detected by default).")
    dl.add_argument("--lon-col", default=None, help="Longitude column (auto-detected by default).")
    dl.add_argument("--buffer", type=float, default=30.0, help="Half-width of query box in meters.")
    dl.add_argument("--max-tiles", type=int, default=None, help="Cap files per product/site; 0 means no cap.")
    dl.add_argument("--max-file-size-mb", type=float, default=512.0, help="Maximum single download size in MiB; 0 disables the size guard.")
    dl.add_argument("--dem-resolution", default="1/3", help="DEM tier for product dem: 1/3, 1/9, 1m, 30m, or auto (default: 1/3).")
    dl.add_argument("--make-points", action="store_true", help="Write a single-point shapefile per site.")
    dl.add_argument("--points-dir", default=None, help="Base directory for point shapefiles; defaults to --download when set.")
    dl.add_argument("--tiger-year", type=int, default=2025, help="Census TIGER/Line vintage year for roads.")
    dl.add_argument("--nlcd-year", type=int, default=2023, help="Annual NLCD land-cover year.")

    manifest_download = sub.add_parser(
        "download-dem-manifest",
        help="Download URL-backed DEM manifest items and update tile paths.",
    )
    manifest_download.add_argument("--manifest", required=True, help="DEM manifest JSON with URL-backed items.")
    manifest_download.add_argument("--out-dir", required=True, help="Directory for downloaded raw DEM tiles.")
    manifest_download.add_argument("--updated-manifest", default=None, help="Optional output manifest path; defaults to updating --manifest.")

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
    mat_dem.add_argument("--source-dir", default=None, help="Directory containing downloaded DEM rasters/zips.")
    mat_dem.add_argument("--out", default=None, help="Output DEM path; defaults to <root>/<site>/demlr/cliped_utm.tif.")
    mat_dem.add_argument("--dst-crs", default=None, help="Target CRS, e.g. EPSG:26912; defaults to UTM inferred from raster center.")
    mat_dem.add_argument("--manifest", default=None, help="DEM download manifest with an explicit tiles list; avoids scanning unrelated rasters.")

    area = sub.add_parser(
        "dem-acquisition-area",
        help="Create an outlet-based initial DEM acquisition polygon for downloader tile selection.",
    )
    area.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    area.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    area.add_argument("--out", required=True, help="Output GeoJSON path for the acquisition polygon.")
    area.add_argument("--upstream-km", type=float, default=25.0, help="Distance from outlet toward upstream end.")
    area.add_argument("--downstream-km", type=float, default=3.0, help="Small downstream margin below the outlet.")
    area.add_argument("--lateral-km", type=float, default=5.0, help="Half-width lateral margin.")
    area.add_argument("--azimuth", type=float, default=None, help="Optional upstream azimuth, degrees clockwise from north, for an oriented rectangle.")

    snap = sub.add_parser(
        "dem-snap-outlet",
        help="Snap an outlet point to the nearest GeoJSON flowline segment.",
    )
    snap.add_argument("--lat", type=float, required=True, help="Raw outlet latitude in EPSG:4326.")
    snap.add_argument("--lon", type=float, required=True, help="Raw outlet longitude in EPSG:4326.")
    snap.add_argument("--flowlines", required=True, help="EPSG:4326 GeoJSON flowlines.")
    snap.add_argument("--out", required=True, help="Output GeoJSON path for the snapped outlet point.")
    snap.add_argument("--snap-distance-m", type=float, default=500.0, help="Maximum allowed snap distance in meters.")

    network_area = sub.add_parser(
        "dem-upstream-network-area",
        help="Create a lightweight upstream-flowline DEM acquisition envelope.",
    )
    network_area.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    network_area.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    network_area.add_argument("--flowlines", required=True, help="EPSG:4326 GeoJSON flowlines used to infer the upstream envelope.")
    network_area.add_argument("--out", required=True, help="Output GeoJSON path for the acquisition polygon.")
    network_area.add_argument("--upstream-trace-km", type=float, default=40.0, help="Maximum outlet-to-flowline vertex distance to consider.")
    network_area.add_argument("--upstream-margin-km", type=float, default=5.0, help="Safety margin beyond the upstream flowline extent.")
    network_area.add_argument("--downstream-margin-km", type=float, default=3.0, help="Safety margin downstream of the outlet.")
    network_area.add_argument("--lateral-margin-km", type=float, default=4.0, help="Safety margin on both sides of the flowline envelope.")
    network_area.add_argument("--envelope-type", default="oriented_rectangle", choices=("oriented_rectangle", "axis_aligned_rectangle"))

    manifest = sub.add_parser(
        "dem-tile-manifest",
        help="Select DEM tile-index features intersecting a DEM acquisition polygon.",
    )
    manifest.add_argument("--acquisition-area", required=True, help="GeoJSON acquisition polygon from dem-acquisition-area or UI drawing.")
    manifest.add_argument("--tile-index", required=True, help="GeoJSON tile footprint/index file with URL/path properties.")
    manifest.add_argument("--out", required=True, help="Output DEM download manifest JSON.")
    manifest.add_argument("--url-field", default="url", help="Tile-index property containing the download URL.")
    manifest.add_argument("--path-field", default="path", help="Tile-index property containing the local raw tile path.")

    boundary = sub.add_parser(
        "dem-boundary-check",
        help="Check whether a delineated watershed is too close to the DEM acquisition boundary.",
    )
    boundary.add_argument("--watershed", required=True, help="Delineated watershed GeoJSON polygon.")
    boundary.add_argument("--acquisition-area", required=True, help="DEM acquisition GeoJSON polygon.")
    boundary.add_argument("--safety-distance-m", type=float, default=500.0)
    boundary.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")

    expand = sub.add_parser(
        "dem-expand-area",
        help="Directionally expand a DEM acquisition polygon after a boundary check fails.",
    )
    expand.add_argument("--acquisition-area", required=True, help="DEM acquisition GeoJSON polygon to expand.")
    expand.add_argument("--out", required=True, help="Output expanded DEM acquisition GeoJSON polygon.")
    expand.add_argument("--edges", required=True, help="Comma-separated edges to expand: west,south,east,north.")
    expand.add_argument("--expansion-distance-km", type=float, default=5.0)

    fetch = sub.add_parser(
        "fetch-phase1-inputs",
        help="Create outlet.shp and download source DEM/hydro products for phase 1.",
    )
    fetch.add_argument("--root", required=True)
    fetch.add_argument("--site", required=True)
    fetch.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    fetch.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    fetch.add_argument("--site-id", default=None, help="Folder-safe ID for source downloads; defaults to the site name.")
    fetch.add_argument("--products", default="all", help="dem, demlr, hydro, roads, landcover/nlcd, atlas14, all, or comma-separated subset (default: all).")
    fetch.add_argument("--download-dir", default=None, help="Raw source download directory; defaults under the site folder.")
    fetch.add_argument("--buffer", type=float, default=500.0, help="Half-width of TNM query box in meters.")
    fetch.add_argument("--max-tiles", type=int, default=None, help="Cap files per product/site; 0 means no cap.")
    fetch.add_argument("--max-file-size-mb", type=float, default=512.0, help="Maximum single download size in MiB; 0 disables the size guard.")
    fetch.add_argument("--skip-outlet", action="store_true", help="Only create folders and download source products.")

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
    all_inputs.add_argument("--max-file-size-mb", type=float, default=512.0, help="Maximum single download size in MiB; 0 disables the size guard.")
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
    materialize.add_argument("--dem-manifest", default=None, help="DEM tile manifest with explicit raw raster paths.")
    materialize.add_argument("--clip-bounds", default=None, help="Optional minx,miny,maxx,maxy materialization bounds.")
    materialize.add_argument("--clip-bounds-crs", default="EPSG:4326", help="CRS for --clip-bounds; defaults to EPSG:4326.")
    materialize.add_argument("--clip-center-lat", type=float, default=None, help="Latitude for auto materialization bounds.")
    materialize.add_argument("--clip-center-lon", type=float, default=None, help="Longitude for auto materialization bounds.")
    materialize.add_argument("--clip-buffer", type=float, default=None, help="Meter buffer around --clip-center-lat/lon for materialization bounds.")
    materialize.add_argument("--clip-buffer-scale", type=float, default=1.2, help="Safety scale applied to --clip-buffer; default 1.2.")

    init_dem = sub.add_parser(
        "init-dem-config",
        help="Write a starter DEM acquisition config from outlet and optional flowline/tile-index paths.",
    )
    init_dem.add_argument("--config", required=True, help="Output YAML/JSON config path.")
    init_dem.add_argument("--site", required=True, help="Site/project name.")
    init_dem.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    init_dem.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    init_dem.add_argument("--flowlines", default=None, help="GeoJSON flowlines for upstream_network mode.")
    init_dem.add_argument("--tile-index", default=None, help="Optional DEM tile-index GeoJSON path.")
    init_dem.add_argument("--target-crs", default=None, help="Optional target CRS; defaults to NAD83 UTM inferred from outlet.")
    init_dem.add_argument("--method", default="upstream_network", choices=("upstream_network", "outlet_buffer", "oriented_outlet_buffer", "polygon"))

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
    bounds.add_argument("--no-web", action="store_true", help="Skip NLDI and use coordinate-buffer bounds.")
    bounds.add_argument("--json", action="store_true")

    init = sub.add_parser("init-inputs", help="Create source-input folders and an INPUTS.md checklist.")
    init.add_argument("--root", required=True)
    init.add_argument("--site", required=True)

    chk = sub.add_parser("check-inputs", help="Verify required GIStoOHQ input files and fields.")
    chk.add_argument("--root", required=True)
    chk.add_argument("--site", required=True)
    chk.add_argument("--config", default=None)
    chk.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")
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
    run.add_argument("--out-dir", default=None, help="Legacy outputs directory; defaults to <root>/<site>/outputs.")
    run.add_argument("--dem-path", default=None, help="Real-elevation DEM path passed to Phase 1 scripts.")
    run.add_argument("--outlet-path", default=None, help="Outlet shapefile path passed to legacy scripts.")
    run.add_argument("--flowline-path", default=None, help="Flowline path passed to legacy scripts.")
    run.add_argument("--flowdir-path", default=None, help="flow_dir.tif path passed to Phase 1 scripts.")
    run.add_argument("--flowacc-path", default=None, help="flow_acc.tif path passed to Phase 1 scripts.")
    run.add_argument("--target-epsg", default=None, help="Target EPSG code forwarded to legacy scripts.")
    run.add_argument("--no-force", action="store_true", help="Forward FORCE=False to legacy scripts.")
    run.add_argument("--prepare-dry-run", action="store_true", help="Run legacy preflight and list steps without executing processing.")
    run.add_argument("--start-at", default=None, help="Resume prepare phase at the named legacy step script.")
    run.add_argument("--skip-prepare", action="store_true")
    run.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")
    run.add_argument("--no-auto-pour-points", action="store_true", help="Require manually supplied pour points.")
    run.add_argument("--no-auto-outlet", action="store_true", help="Require a manually supplied outlet.")

    full = sub.add_parser(
        "full-run",
        help="Download source data and build an OHQ project in one command.",
    )
    full.add_argument("--root", required=True)
    full.add_argument("--site", required=True)
    full.add_argument("--lat", type=float, required=True, help="Approximate outlet latitude.")
    full.add_argument("--lon", type=float, required=True, help="Approximate outlet longitude.")
    full.add_argument("--project-name", default=None)
    full.add_argument("--out", default=None)
    full.add_argument("--script-dir", default=None)
    full.add_argument("--buffer", type=float, default=5000.0, help="Source-data query buffer in meters.")
    full.add_argument("--target-crs", default=None, help="Optional DEM target CRS, e.g. EPSG:26912.")
    full.add_argument("--site-id", default=None, help="Folder-safe source download ID.")
    full.add_argument("--download-dir", default=None, help="Override the raw download directory.")
    full.add_argument("--max-tiles", type=int, default=None, help="Cap files per product; 0 means no cap.")
    full.add_argument("--max-file-size-mb", type=float, default=512.0, help="Maximum single download size in MiB; 0 disables the size guard.")
    full.add_argument("--soil-pixel-size", type=float, default=0.0003)
    full.add_argument("--soil-top-depth", type=float, default=30.0)

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


def _validate_inputs(settings: BuilderSettings, no_schema: bool, json_output: bool = False) -> int:
    result = InputValidator().validate(settings, check_schema=not no_schema)
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        _print_input_result(result)
        if result.ok:
            print("Input validation OK")
    return 0 if result.ok else 2


def _maybe_validate_inputs(settings: BuilderSettings, skip_input_check: bool, no_schema: bool) -> int:
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
        junctions = Path(args.junctions).expanduser() if args.junctions else outputs / "junctions.gpkg"
        output = Path(args.out).expanduser() if args.out else outputs / "pour_points.shp"
        try:
            result = generate_pour_points(junctions, output, overwrite=args.overwrite)
        except PourPointGenerationError as exc:
            print(f"create-pour-points failed: {exc}")
            return 2
        print(f"Generated {result.count} pour point(s): {result.output_path}")
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
                progress=lambda message: print(message, flush=True),
            )
        except FullRunError as exc:
            print(f"full-run failed: {exc}")
            return 2
        print(f"Full pipeline complete: {result.output_path}")
        return 0
    if args.command == "download-data":
        try:
            default_output = str(Path(args.input_csv).with_name(Path(args.input_csv).stem + "_dem.csv"))
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
        landcover = getattr(result, "landcover", None)
        if landcover is not None:
            print(f"Wrote landcover: {landcover}")
        cn_lookup = getattr(result, "cn_lookup", None)
        if cn_lookup is not None:
            print(f"Wrote CN lookup: {cn_lookup}")
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
            )
        except (DemWorkflowError, ValueError) as exc:
            print(f"init-dem-config failed: {exc}")
            return 2
        print(f"Wrote DEM config: {path}")
        print("Next: ohqbuild prepare-dem --config " + str(path))
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
        print(f"Touched edges: {','.join(result.touched_edges) if result.touched_edges else 'none'}")
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
            print(json.dumps({
                "is_valid": result.is_valid,
                "touched_edges": result.touched_edges,
                "distances_m": result.distances_m,
            }, indent=2, sort_keys=True))
        else:
            print(f"Boundary validation: {'OK' if result.is_valid else 'EXPAND'}")
            print(f"Touched edges: {','.join(result.touched_edges) if result.touched_edges else 'none'}")
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

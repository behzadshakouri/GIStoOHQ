from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dem_downloader import parse_products, process_csv
from .dem_materializer import DemMaterializeError, materialize_dem
from .doctor import run_doctor
from .legacy_inputs import LegacyInputWorkflowError, LegacyWorkflowOptions, run_legacy_input_workflow, write_input_manifest
from .phase1_fetcher import Phase1FetchError, fetch_phase1_inputs
from .pour_points import PourPointGenerationError, generate_pour_points
from .outlet_creator import OutletCreationError, create_outlet_from_flow_accumulation
from .pipeline import build_ohq_project
from .settings import BuilderSettings
from .soil_retrieval import SoilRetrievalError, retrieve_hydrologic_soil_groups, retrieve_soil_texture
from .validation.input_validator import InputValidator


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
    prep.add_argument("--no-auto-pour-points", action="store_true", help="Require an existing pour_points.shp instead of generating it from Phase 1 junctions.")
    prep.add_argument("--no-auto-outlet", action="store_true", help="Require an existing outlet.shp instead of deriving it from flow_acc.tif.")

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

    dl = sub.add_parser("download-data", help="Query/download USGS DEM and hydrography products for site coordinates.")
    dl.add_argument("input_csv", help="CSV with WGS84 latitude/longitude columns.")
    dl.add_argument("output_csv", nargs="?", default=None, help="Optional CSV summary to write.")
    dl.add_argument("--products", default="dem", help="dem, hydro, all, or comma-separated subset (default: dem).")
    dl.add_argument("--download", default=None, help="Directory for per-site downloads.")
    dl.add_argument("--id-col", default=None, help="Column used for per-site folder names.")
    dl.add_argument("--lat-col", default=None, help="Latitude column (auto-detected by default).")
    dl.add_argument("--lon-col", default=None, help="Longitude column (auto-detected by default).")
    dl.add_argument("--buffer", type=float, default=30.0, help="Half-width of query box in meters.")
    dl.add_argument("--max-tiles", type=int, default=None, help="Cap files per product/site; 0 means no cap.")

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

    fetch = sub.add_parser(
        "fetch-phase1-inputs",
        help="Create outlet.shp and download source DEM/hydro products for phase 1.",
    )
    fetch.add_argument("--root", required=True)
    fetch.add_argument("--site", required=True)
    fetch.add_argument("--lat", type=float, required=True, help="Outlet latitude in EPSG:4326.")
    fetch.add_argument("--lon", type=float, required=True, help="Outlet longitude in EPSG:4326.")
    fetch.add_argument("--site-id", default=None, help="Folder-safe ID for source downloads; defaults to the site name.")
    fetch.add_argument("--products", default="all", help="dem, hydro, all, or comma-separated subset (default: all).")
    fetch.add_argument("--download-dir", default=None, help="Raw source download directory; defaults under the site folder.")
    fetch.add_argument("--buffer", type=float, default=500.0, help="Half-width of TNM query box in meters.")
    fetch.add_argument("--max-tiles", type=int, default=None, help="Cap files per product/site; 0 means no cap.")
    fetch.add_argument("--skip-outlet", action="store_true", help="Only create folders and download source products.")

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
    run.add_argument("--skip-prepare", action="store_true")
    run.add_argument("--no-schema", action="store_true", help="Only check that required files exist.")
    run.add_argument("--no-auto-pour-points", action="store_true", help="Require manually supplied pour points.")
    run.add_argument("--no-auto-outlet", action="store_true", help="Require a manually supplied outlet.")

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
    if args.command == "download-data":
        try:
            results = process_csv(
                args.input_csv,
                args.output_csv,
                products=parse_products(args.products),
                download_dir=args.download,
                id_col=args.id_col,
                lat_col=args.lat_col,
                lon_col=args.lon_col,
                buffer_m=args.buffer,
                max_tiles=args.max_tiles,
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
            )
        except DemMaterializeError as exc:
            print(f"materialize-dem failed: {exc}")
            return 2
        print(f"Wrote DEM: {result.output_path}")
        print(f"Source product count: {result.source_count}")
        print(f"Target CRS: {result.dst_crs}")
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

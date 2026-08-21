from __future__ import annotations

from pathlib import Path

import yaml


def _read_config(path: Path):
    import json
    import yaml

    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_config(path: Path, data) -> None:
    import json
    import yaml

    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_geojson_polygon(path: Path, coords: list[tuple[float, float]], *, source: str) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"source": source},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[list(point) for point in coords]],
                        },
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_manifest_footprints(manifest_path: Path) -> Path | None:
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get("items")
    if not isinstance(items, list):
        return None
    features = []
    for item in items:
        if not isinstance(item, dict):
            continue
        bounds = item.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            continue
        minx, miny, maxx, maxy = (float(value) for value in bounds)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "path": item.get("path", ""),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [minx, miny],
                            [maxx, miny],
                            [maxx, maxy],
                            [minx, maxy],
                            [minx, miny],
                        ]
                    ],
                },
            }
        )
    if not features:
        return None
    output = manifest_path.with_name(manifest_path.stem + "_footprints.geojson")
    output.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=2),
        encoding="utf-8",
    )
    return output


class QgisDockConfigError(RuntimeError):
    """Raised when the QGIS dock cannot build a backend command from config."""


def _selected_station_from_reconnaissance(path: str) -> str:
    import json

    candidate = Path(path).expanduser().resolve()
    report_path = candidate / "report.json" if candidate.is_dir() else candidate
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QgisDockConfigError(f"Could not read reconnaissance report: {exc}") from exc
    if report.get("schema_name") != "ReconnaissanceReport":
        raise QgisDockConfigError("Selected gauge requires a ReconnaissanceReport.")
    if report.get("decision") != "selected" or not report.get("selected_station_id"):
        raise QgisDockConfigError(
            f"Reconnaissance has no unambiguous selection: {report.get('decision', 'unknown')}"
        )
    station = str(report["selected_station_id"])
    if not station.isdigit():
        raise QgisDockConfigError("Selected USGS station ID must contain digits only.")
    return station


def _select_catalog_asset(catalog_path: str, product: str) -> str:
    import hashlib
    import json

    if not product.strip():
        raise QgisDockConfigError("Asset product is required.")
    try:
        document = json.loads(Path(catalog_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QgisDockConfigError(f"Could not read asset catalog: {exc}") from exc
    canonical = json.dumps(
        document.get("assets", []), ensure_ascii=False, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != document.get("catalog_digest"):
        raise QgisDockConfigError("Asset catalog digest does not match its assets.")
    matches = [
        asset for asset in document.get("assets", [])
        if asset.get("product") == product and asset.get("processing_status") == "native"
    ]
    if not matches:
        raise QgisDockConfigError(f"Catalog has no native asset for product {product!r}.")
    selected = max(matches, key=lambda item: (item.get("registered_at", ""), item["asset_id"]))
    return str(selected["asset_id"])


def _as_mapping(value, name: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise QgisDockConfigError(f"Config section {name!r} must be an object.")


def _relative_to_config(config_path: Path, value) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    base = config_path.expanduser().resolve().parent
    cwd_candidate = path.resolve()
    try:
        cwd_candidate.relative_to(base)
        return cwd_candidate
    except ValueError:
        return base / path


def _site_name(config: dict) -> str:
    site = config.get("site")
    if isinstance(site, dict):
        name = site.get("name")
        if name:
            return str(name)
    if isinstance(site, str) and site:
        return site
    return "."


def _target_crs(config: dict) -> str | None:
    site = config.get("site")
    if isinstance(site, dict) and site.get("target_crs"):
        return str(site["target_crs"])
    if config.get("target_crs"):
        return str(config["target_crs"])
    return None


def _command_for_watershed_data(
    action: str,
    *,
    site_spec: str,
    site_id: str = "",
    name: str = "",
    longitude: float | None = None,
    latitude: float | None = None,
    start: str = "",
    end: str = "",
    url: str = "",
    provider: str = "",
    product: str = "",
    product_version: str = "unspecified",
    cache: str = "",
    catalog: str = "",
    package: str = "",
    include_raw: str = "referenced",
    reconnaissance_output: str = "reconnaissance",
    radius_km: float = 50.0,
    station_id: str = "",
    weather_variables: str = "PRECTOTCORR,T2M,RH2M,WS2M,ALLSKY_SFC_SW_DWN",
    asset_id: str = "",
    qc_output: str = "watershed_package/quality_control/temporal.json",
    provenance_output: str = "watershed_package/provenance/temporal.json",
    hydropinn_output: str = "outputs/hydropinn",
    workspace: str = "watershed_data_run",
    forecast_url: str = "", forecast_provider: str = "",
    forecast_product: str = "forecast", prediction_time: str = "",
    status_output: str = "watershed_package/status",
    refresh: bool = False,
) -> list[str]:
    """Build optional data commands without coupling them to full-run."""
    if action == "init-site":
        required = {
            "site specification": site_spec, "site ID": site_id, "start": start, "end": end,
        }
        missing = [label for label, value in required.items() if value in (None, "")]
        if longitude is None or latitude is None:
            missing.append("outlet coordinates")
        if missing:
            raise QgisDockConfigError("Missing watershed data fields: " + ", ".join(missing))
        command = [
            "ohqbuild", "data", "init-site", "--site-spec", site_spec,
            "--site-id", site_id, "--lon", str(longitude), "--lat", str(latitude),
            "--start", start, "--end", end,
        ]
        if name:
            command.extend(["--name", name])
        return command
    if action == "validate-site":
        if not site_spec:
            raise QgisDockConfigError("A site specification path is required.")
        return ["ohqbuild", "data", "validate-site", "--site-spec", site_spec]
    if action == "acquire-url":
        values = {
            "URL": url, "provider": provider, "product": product,
            "cache": cache, "catalog": catalog,
        }
        missing = [label for label, value in values.items() if not value]
        if missing:
            raise QgisDockConfigError("Missing watershed data fields: " + ", ".join(missing))
        command = [
            "ohqbuild", "data", "acquire-url", "--url", url,
            "--provider", provider, "--product", product,
            "--product-version", product_version or "unspecified",
            "--cache", cache, "--catalog", catalog,
        ]
        return [*command, "--refresh"] if refresh else command
    if action == "freeze":
        values = {"SiteSpec": site_spec, "catalog": catalog, "package": package}
        missing = [label for label, value in values.items() if not value]
        if missing:
            raise QgisDockConfigError("Missing watershed data fields: " + ", ".join(missing))
        return [
            "ohqbuild", "data", "freeze", "--site-spec", site_spec,
            "--catalog", catalog, "--output", package, "--include-raw", include_raw,
        ]
    if action == "validate-package":
        if not package:
            raise QgisDockConfigError("A package directory is required.")
        return ["ohqbuild", "data", "validate-package", "--package", package]
    if action == "reconnaissance":
        if not site_spec or not reconnaissance_output:
            raise QgisDockConfigError("SiteSpec and reconnaissance output are required.")
        return [
            "ohqbuild", "data", "reconnaissance", "--site-spec", site_spec,
            "--output", reconnaissance_output, "--radius-km", str(radius_km),
        ]
    if action == "download-discharge":
        values = {
            "SiteSpec": site_spec, "station ID": station_id,
            "cache": cache, "catalog": catalog,
        }
        missing = [label for label, value in values.items() if not value]
        if missing:
            raise QgisDockConfigError("Missing watershed data fields: " + ", ".join(missing))
        command = [
            "ohqbuild", "data", "download-discharge", "--site-spec", site_spec,
            "--station-id", station_id, "--cache", cache, "--catalog", catalog,
        ]
        return [*command, "--refresh"] if refresh else command
    if action == "download-weather":
        if not site_spec or not cache or not catalog or not weather_variables:
            raise QgisDockConfigError("SiteSpec, cache, catalog, and weather variables are required.")
        command = [
            "ohqbuild", "data", "download-weather", "--site-spec", site_spec,
            "--cache", cache, "--catalog", catalog, "--variables", weather_variables,
        ]
        return [*command, "--refresh"] if refresh else command
    if action == "harmonize":
        if not asset_id:
            raise QgisDockConfigError("A native catalog asset ID is required.")
        return [
            "ohqbuild", "data", "harmonize", "--asset-id", asset_id,
            "--catalog", catalog, "--object-store", cache,
            "--qc-output", qc_output, "--provenance-output", provenance_output,
        ]
    if action == "download-pet":
        command = [
            "ohqbuild", "data", "download-pet", "--site-spec", site_spec,
            "--cache", cache, "--catalog", catalog, "--variables", "EVPTRNS",
        ]
        return [*command, "--refresh"] if refresh else command
    if action == "export-hydropinn":
        return [
            "ohqbuild", "data", "export-hydropinn", "--package", package,
            "--object-store", cache, "--output", hydropinn_output,
        ]
    if action == "run":
        if not station_id:
            raise QgisDockConfigError("Select an explicit USGS station ID before running all steps.")
        bootstrap = _data_bootstrap_args(site_id, name, longitude, latitude, start, end)
        forecast_args = _data_forecast_run_args(
            forecast_url, forecast_provider, forecast_product, prediction_time
        )
        return [
            "ohqbuild", "data", "run", "--site-spec", site_spec,
            "--station-id", station_id, "--workspace", workspace,
            "--export-hydropinn", *(["--refresh"] if refresh else []),
            *forecast_args, *bootstrap,
        ]
    if action == "run-weather":
        bootstrap = _data_bootstrap_args(site_id, name, longitude, latitude, start, end)
        forecast_args = _data_forecast_run_args(
            forecast_url, forecast_provider, forecast_product, prediction_time
        )
        return [
            "ohqbuild", "data", "run", "--site-spec", site_spec,
            "--station-id", "", "--workspace", workspace,
            "--no-discharge", "--export-hydropinn", *(["--refresh"] if refresh else []),
            *forecast_args, *bootstrap,
        ]
    if action == "download-forecast":
        if not forecast_url or not forecast_provider:
            raise QgisDockConfigError("Forecast URL and provider are required.")
        command = ["ohqbuild", "data", "download-forecast", "--url", forecast_url,
                "--provider", forecast_provider, "--product", forecast_product,
                "--cache", cache, "--catalog", catalog]
        return [*command, "--refresh"] if refresh else command
    if action == "forecast-view":
        if not asset_id or not prediction_time:
            raise QgisDockConfigError("Forecast asset ID and prediction time are required.")
        return ["ohqbuild", "data", "forecast-view", "--asset-id", asset_id,
                "--prediction-time", prediction_time, "--object-store", cache,
                "--catalog", catalog]
    if action == "status":
        return ["ohqbuild", "data", "status", "--catalog", catalog,
                "--object-store", cache, "--output", status_output]
    if action == "doctor":
        return ["ohqbuild", "data", "doctor", "--site-spec", site_spec,
                "--catalog", catalog, "--object-store", cache, "--package", package]
    if action == "gc":
        return ["ohqbuild", "data", "gc", "--object-store", cache,
                "--catalog", catalog, "--output", str(Path(status_output) / "cache-gc.json")]
    raise QgisDockConfigError(f"Unknown watershed data action: {action}")


def _data_bootstrap_args(site_id, name, longitude, latitude, start, end) -> list[str]:
    if any(value in (None, "") for value in (site_id, longitude, latitude, start, end)):
        raise QgisDockConfigError(
            "One-button data runs require site ID, outlet coordinates, and study start/end."
        )
    args = [
        "--init-if-missing", "--site-id", str(site_id), "--lon", str(longitude),
        "--lat", str(latitude), "--start", str(start), "--end", str(end),
    ]
    if name:
        args.extend(["--name", str(name)])
    return args


def _data_forecast_run_args(url, provider, product, prediction_time) -> list[str]:
    if bool(url) != bool(provider):
        raise QgisDockConfigError("Forecast URL and provider must be supplied together.")
    if prediction_time and not url:
        raise QgisDockConfigError("Prediction time requires a forecast URL and provider.")
    if not url:
        return []
    args = [
        "--forecast-url", url, "--forecast-provider", provider,
        "--forecast-product", product or "forecast",
    ]
    if prediction_time:
        args.extend(["--prediction-time", prediction_time])
    return args


def _command_for_workflow(
    command: str,
    config_text: str,
    *,
    use_reviewed_pour_points: bool | None = None,
    nhdplus_snap_distance_m: float | None = None,
    minimum_watershed_area_km2: float | None = None,
    minimum_subwatershed_area_km2: float | None = None,
    minimum_area_ratio: float | None = None,
    maximum_area_ratio: float | None = None,
    overwrite_promoted_pour_points: bool = False,
    use_existing_outlet: bool = False,
    reuse_downloads: bool = False,
) -> list[str]:
    config_path = Path(config_text).expanduser()
    config = _read_config(config_path)
    if not isinstance(config, dict):
        raise QgisDockConfigError("Project config must contain a JSON/YAML object.")
    dem = _as_mapping(config.get("dem_acquisition"), "dem_acquisition")

    if command in {"prepare-dem", "run-dem-prep", "validate-dem"}:
        return ["ohqbuild", command, "--config", str(config_path)]

    if command == "download-dem-manifest":
        manifest = _relative_to_config(config_path, dem.get("tile_manifest"))
        if manifest is None:
            raise QgisDockConfigError(
                "dem_acquisition.tile_manifest is required to download DEM tiles."
            )
        paths = _as_mapping(config.get("paths"), "paths")
        out_dir = _relative_to_config(
            config_path,
            paths.get("raw_dem_dir") or dem.get("raw_dem_dir") or "dem/raw",
        )
        return ["ohqbuild", command, "--manifest", str(manifest), "--out-dir", str(out_dir)]

    if command == "materialize-inputs":
        root = _relative_to_config(config_path, config.get("root") or ".")
        argv = ["ohqbuild", command, "--root", str(root), "--site", _site_name(config)]
        source_dir = _relative_to_config(
            config_path,
            config.get("download_dir") or config.get("source_dir") or "source_downloads",
        )
        if source_dir is not None:
            argv.extend(["--source-dir", str(source_dir)])
        target_crs = _target_crs(config)
        if target_crs:
            argv.extend(["--target-crs", target_crs])
        manifest = _relative_to_config(config_path, dem.get("tile_manifest"))
        if manifest is not None:
            argv.extend(["--dem-manifest", str(manifest)])
        return argv

    if command in {"prepare-hydrology", "prepare-inputs", "check-inputs", "build"}:
        root = _relative_to_config(config_path, config.get("root") or ".")
        return ["ohqbuild", command, "--root", str(root), "--site", _site_name(config)]

    if command == "create-pour-points":
        root = _relative_to_config(config_path, config.get("root") or ".")
        return [
            "ohqbuild", "create-pour-points", "--root", str(root),
            "--site", _site_name(config), "--overwrite",
        ]

    if command == "promote-pour-points":
        root = _relative_to_config(config_path, config.get("root") or ".")
        argv = [
            "ohqbuild", "promote-pour-points", "--root", str(root),
            "--site", _site_name(config),
        ]
        if overwrite_promoted_pour_points:
            argv.append("--overwrite")
        return argv

    if command == "import-watershed-reference":
        root = _relative_to_config(config_path, config.get("root") or ".")
        outlet = _as_mapping(config.get("outlet"), "outlet")
        reference = _as_mapping(
            config.get("documented_watershed"), "documented_watershed"
        )
        required = {
            "outlet.longitude": outlet.get("longitude"),
            "outlet.latitude": outlet.get("latitude"),
            "documented_watershed.source": reference.get("source"),
            "documented_watershed.title": reference.get("title"),
            "documented_watershed.organization": reference.get("organization"),
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise QgisDockConfigError(
                "Documented watershed import requires config values: "
                + ", ".join(missing)
            )
        source = str(reference["source"])
        if not source.lower().startswith(("http://", "https://")):
            source = str(_relative_to_config(config_path, source))
        argv = [
            "ohqbuild", "import-watershed-reference",
            "--root", str(root), "--site", _site_name(config),
            "--source", source,
            "--lon", str(float(outlet["longitude"])),
            "--lat", str(float(outlet["latitude"])),
            "--source-title", str(reference["title"]),
            "--source-organization", str(reference["organization"]),
        ]
        for flag, key in (
            ("--layer", "layer"), ("--name-field", "name_field"),
            ("--name", "name"), ("--source-url", "url"),
            ("--license", "license"),
        ):
            if reference.get(key):
                argv.extend([flag, str(reference[key])])
        return argv

    if command == "build-hms":
        root = _relative_to_config(config_path, config.get("root") or ".")
        return [
            "ohqbuild",
            "build-hms",
            "--root",
            str(root),
            "--site",
            _site_name(config),
            "--project-name",
            _site_name(config),
        ]

    if command == "validate-hms":
        root = _relative_to_config(config_path, config.get("root") or ".")
        project = root / _site_name(config) / "outputs" / "hec_hms" / f"{_site_name(config)}.hms"
        return ["ohqbuild", "validate-hms", "--project", str(project)]

    if command == "full-run":
        outlet = _as_mapping(config.get("outlet"), "outlet")
        outlet_source = outlet.get("source") or outlet.get("kmz") or outlet.get("kml")
        if not outlet_source and (
            outlet.get("longitude") is None or outlet.get("latitude") is None
        ):
            raise QgisDockConfigError(
                "Choose outlet.source KML/KMZ or provide outlet.longitude and outlet.latitude for full-run."
            )
        root = _relative_to_config(config_path, config.get("root") or ".")
        argv = [
            "ohqbuild",
            "full-run",
            "--root",
            str(root),
            "--site",
            _site_name(config),
            "--project-name",
            _site_name(config),
            "--config",
            str(config_path),
        ]
        if outlet.get("longitude") is not None and outlet.get("latitude") is not None:
            argv.extend(["--lon", str(float(outlet["longitude"])), "--lat", str(float(outlet["latitude"]))])
        if outlet_source:
            source = str(outlet_source)
            if not source.lower().startswith(("http://", "https://")):
                source = str(_relative_to_config(config_path, source))
            argv.extend(["--outlet-source", source])
        if outlet.get("snap_to_documented_watershed"):
            argv.append("--snap-outlet-to-documented-watershed")
        target_crs = _target_crs(config)
        if target_crs:
            argv.extend(["--target-crs", target_crs])
        source_dir = _relative_to_config(
            config_path, config.get("download_dir") or "source_downloads"
        )
        if source_dir is not None:
            argv.extend(["--download-dir", str(source_dir)])
        snap_distance = (
            nhdplus_snap_distance_m
            if nhdplus_snap_distance_m is not None
            else config.get("nhdplus_snap_distance_m", 50.0)
        )
        argv.extend(["--nhdplus-snap-distance-m", str(snap_distance)])
        thresholds = _as_mapping(config.get("subwatersheds"), "subwatersheds")
        minimum_area = (minimum_watershed_area_km2 if minimum_watershed_area_km2 is not None
                        else float(thresholds.get("minimum_area_km2", 0.05)))
        ratio_min = (minimum_area_ratio if minimum_area_ratio is not None
                     else float(thresholds.get("area_ratio_min", 0.75)))
        ratio_max = (maximum_area_ratio if maximum_area_ratio is not None
                     else float(thresholds.get("area_ratio_max", 1.25)))
        minimum_subarea = (
            minimum_subwatershed_area_km2
            if minimum_subwatershed_area_km2 is not None
            else float(thresholds.get("minimum_incremental_area_km2", 0.0005))
        )
        if minimum_area < 0 or minimum_subarea < 0 or ratio_min <= 0 or ratio_max < ratio_min:
            raise QgisDockConfigError("Invalid subwatershed area thresholds.")
        argv.extend([
            "--minimum-watershed-area-km2", str(minimum_area),
            "--minimum-subwatershed-area-km2", str(minimum_subarea),
            "--minimum-area-ratio", str(ratio_min),
            "--maximum-area-ratio", str(ratio_max),
        ])
        use_reviewed = (
            use_reviewed_pour_points
            if use_reviewed_pour_points is not None
            else bool(config.get("use_reviewed_pour_points"))
        )
        if use_reviewed:
            argv.append("--use-reviewed-pour-points")
        if use_existing_outlet:
            argv.append("--use-existing-outlet")
        if reuse_downloads:
            argv.append("--reuse-downloads")
        reference = _as_mapping(
            config.get("documented_watershed"), "documented_watershed"
        )
        reference_source = reference.get("source")
        if reference_source:
            source = str(reference_source)
            if not source.lower().startswith(("http://", "https://")):
                source = str(_relative_to_config(config_path, source))
            argv.extend(["--documented-watershed-source", source])
            for flag, key in (
                ("--documented-watershed-layer", "layer"),
                ("--documented-watershed-name-field", "name_field"),
                ("--documented-watershed-name", "name"),
                ("--documented-watershed-title", "title"),
                ("--documented-watershed-organization", "organization"),
                ("--documented-watershed-url", "url"),
                ("--documented-watershed-license", "license"),
            ):
                if reference.get(key):
                    argv.extend([flag, str(reference[key])])
            if reference.get("allow_outlet_outside"):
                argv.append("--documented-watershed-allow-outlet-outside")
        acquisition = _relative_to_config(config_path, dem.get("acquisition_area"))
        if acquisition is not None and (
            acquisition.is_file()
            or dem.get("method")
            in {
                "outlet_buffer",
                "oriented_outlet_buffer",
                "upstream_network",
                "documented_watershed",
            }
        ):
            argv.extend(["--acquisition-area", str(acquisition)])
        return argv

    raise QgisDockConfigError(f"Unsupported workflow command: {command}")


class OutletCaptureTool:
    def __init__(self, dock):
        from qgis.gui import QgsMapToolEmitPoint

        self.dock = dock
        self.tool = QgsMapToolEmitPoint(dock.iface.mapCanvas())
        self.tool.canvasClicked.connect(self.capture)

    def activate(self):
        self.dock.iface.mapCanvas().setMapTool(self.tool)
        self.dock.log.append("Click the outlet point on the map canvas.")

    def capture(self, point, button):
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        canvas = self.dock.iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        lonlat = transform.transform(point)
        self.dock.write_outlet(lonlat.x(), lonlat.y())
        self.dock.log.append(f"Outlet set to lon={lonlat.x():.8f}, lat={lonlat.y():.8f}")


class PourPointCaptureTool:
    """Capture any number of reviewable interior pour points from the canvas."""

    def __init__(self, dock):
        from qgis.gui import QgsMapToolEmitPoint

        self.dock = dock
        self.tool = QgsMapToolEmitPoint(dock.iface.mapCanvas())
        self.tool.canvasClicked.connect(self.capture)

    def activate(self):
        self.dock.iface.mapCanvas().setMapTool(self.tool)
        self.dock.log.append(
            "Left-click interior pour points; right-click to finish. "
            "New points are pending until their attributes are reviewed."
        )

    def capture(self, point, button):
        from qgis.PyQt.QtCore import Qt

        if button == Qt.RightButton:
            self.dock.finish_pour_point_capture()
            return
        self.dock.add_pour_point_from_canvas(point)


class AcquisitionPolygonTool:
    def __init__(self, dock):
        from qgis.gui import QgsMapToolEmitPoint

        self.dock = dock
        self.points = []
        self.tool = QgsMapToolEmitPoint(dock.iface.mapCanvas())
        self.tool.canvasClicked.connect(self.capture)

    def activate(self):
        self.points = []
        self.dock.iface.mapCanvas().setMapTool(self.tool)
        self.dock.log.append("Left-click DEM area vertices; right-click to finish polygon.")

    def capture(self, point, button):
        from qgis.PyQt.QtCore import Qt
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        if button == Qt.RightButton:
            self.finish()
            return
        canvas = self.dock.iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        lonlat = transform.transform(point)
        self.points.append((lonlat.x(), lonlat.y()))
        self.dock.log.append(
            f"Added DEM area vertex {len(self.points)}: {lonlat.x():.8f}, {lonlat.y():.8f}"
        )

    def finish(self):
        if len(self.points) < 3:
            self.dock.log.append("Need at least three vertices for DEM acquisition polygon.")
            return
        coords = [*self.points, self.points[0]]
        self.dock.write_acquisition_polygon(coords, "qgis_drawn_polygon")
        self.dock.log.append("Wrote DEM acquisition polygon from clicked vertices.")


class DemWorkflowDock:
    """QGIS dock skeleton that delegates workflow work to ohqbuild commands."""

    def __init__(self, iface):
        from qgis.PyQt.QtWidgets import (
            QDockWidget,
            QWidget,
            QVBoxLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QCheckBox,
            QDoubleSpinBox,
            QPushButton,
            QTextEdit,
            QTabWidget,
            QGridLayout,
            QGroupBox,
        )

        self.iface = iface
        self.widget = QDockWidget("GIStoOHQ DEM Workflow", iface.mainWindow())
        self.panel = QWidget(self.widget)
        layout = QVBoxLayout(self.panel)
        row = QHBoxLayout()
        row.addWidget(QLabel("Config"))
        self.config = QLineEdit("config.example.json")
        row.addWidget(self.config)
        layout.addLayout(row)
        options_box = QGroupBox("Run options")
        controls = QGridLayout(options_box)
        self.reviewed_points = QCheckBox("Use reviewed pour points")
        controls.addWidget(self.reviewed_points, 0, 0)
        controls.addWidget(QLabel("Snap max (m)"), 0, 1)
        self.nhdplus_snap_distance = QDoubleSpinBox()
        self.nhdplus_snap_distance.setRange(0.0, 100000.0)
        self.nhdplus_snap_distance.setValue(50.0)
        controls.addWidget(self.nhdplus_snap_distance, 0, 2)
        self.overwrite_promoted = QCheckBox("Overwrite promoted points")
        controls.addWidget(self.overwrite_promoted, 1, 0)
        self.use_existing_outlet = QCheckBox("Use edited outlet.shp")
        controls.addWidget(self.use_existing_outlet, 1, 1)
        self.reuse_downloads = QCheckBox("Offline: reuse downloads")
        controls.addWidget(self.reuse_downloads, 1, 2)
        controls.addWidget(QLabel("Min watershed (km²)"), 2, 0)
        self.minimum_watershed_area = QDoubleSpinBox()
        self.minimum_watershed_area.setRange(0.0, 100000.0)
        self.minimum_watershed_area.setDecimals(4)
        self.minimum_watershed_area.setValue(0.05)
        controls.addWidget(self.minimum_watershed_area, 2, 1)
        self.minimum_subwatershed_area = QDoubleSpinBox()
        self.minimum_subwatershed_area.setRange(0.0, 100000.0)
        self.minimum_subwatershed_area.setDecimals(4)
        self.minimum_subwatershed_area.setValue(0.0005)
        controls.addWidget(QLabel("Min subwatershed (km²)"), 2, 2)
        controls.addWidget(self.minimum_subwatershed_area, 2, 3)
        controls.addWidget(QLabel("Area ratio min/max"), 3, 0)
        self.minimum_area_ratio = QDoubleSpinBox()
        self.minimum_area_ratio.setRange(0.01, 100.0)
        self.minimum_area_ratio.setValue(0.75)
        controls.addWidget(self.minimum_area_ratio, 3, 1)
        self.maximum_area_ratio = QDoubleSpinBox()
        self.maximum_area_ratio.setRange(0.01, 100.0)
        self.maximum_area_ratio.setValue(1.25)
        controls.addWidget(self.maximum_area_ratio, 3, 2)
        layout.addWidget(options_box)

        tabs = QTabWidget()
        layout.addWidget(tabs)
        map_tab = QWidget()
        map_grid = QGridLayout(map_tab)
        for index, (label, callback) in enumerate((
            ("Pick Outlet on Map", self.pick_outlet),
            ("Set Outlet Coordinates", self.set_outlet_coordinates),
            ("Pick Pour Points on Map", self.pick_pour_points),
            ("Add Pour Point Coordinates", self.add_pour_point_coordinates),
            ("Use Canvas Extent as DEM Area", self.use_canvas_extent_as_area),
            ("Draw DEM Area Polygon", self.draw_acquisition_polygon),
            ("Load Configured Layers", self.load_configured_layers),
        )):
            button = QPushButton(label)
            button.clicked.connect(callback)
            map_grid.addWidget(button, index // 2, index % 2)
        tabs.addTab(map_tab, "Map")

        self.action_buttons = {}
        groups = (
            ("Workflow", (
                ("Prepare DEM", "prepare-dem"),
                ("Run Direct DEM Prep", "run-dem-prep"),
                ("Download DEM tiles", "download-dem-manifest"),
                ("Materialize inputs", "materialize-inputs"),
                ("Validate DEM", "validate-dem"),
                ("Prepare hydrology", "prepare-hydrology"),
                ("Prepare GIS inputs", "prepare-inputs"),
                ("Generate Upstream Pour Points", "create-pour-points"),
                ("Check inputs", "check-inputs"),
                ("FULL RUN: Download All Data to OHQ", "full-run"),
            )),
            ("Review", (
                ("Promote Reviewed Pour Points", "promote-pour-points"),
                ("Import Documented Watershed", "import-watershed-reference"),
            )),
            ("Model", (
                ("Build OHQ", "build"),
                ("Build HEC-HMS", "build-hms"),
                ("Validate HEC-HMS", "validate-hms"),
            )),
        )
        for tab_name, actions in groups:
            tab = QWidget()
            grid = QGridLayout(tab)
            for index, (label, command) in enumerate(actions):
                button = QPushButton(label)
                button.clicked.connect(
                    lambda checked=False, value=command: self.run_command(value)
                )
                grid.addWidget(button, index // 2, index % 2)
                self.action_buttons[command] = button
            if tab_name == "Review":
                reference_button = QPushButton("Configure Documented Watershed…")
                reference_button.clicked.connect(self.configure_documented_watershed)
                grid.addWidget(reference_button, 1, 0, 1, 2)
            tabs.addTab(tab, tab_name)
        data_tab = QWidget()
        data_grid = QGridLayout(data_tab)
        data_grid.addWidget(QLabel(
            "Optional discharge, weather, PET/ET, and future forecast data. "
            "This workflow is independent of Full Run to OHQ."
        ), 0, 0, 1, 2)
        data_button = QPushButton("Open Watershed Data…")
        data_button.clicked.connect(self.configure_watershed_data)
        data_grid.addWidget(data_button, 1, 0, 1, 2)
        tabs.insertTab(2, data_tab, "Data")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        layout.addWidget(self.log)
        self.process = None
        self.widget.setWidget(self.panel)
        self._refresh_action_buttons()

    def __getattr__(self, name):
        return getattr(self.widget, name)

    def pick_outlet(self) -> None:
        self.outlet_tool = OutletCaptureTool(self)
        self.outlet_tool.activate()

    def configure_watershed_data(self) -> None:
        """Open the optional data workflow without adding requirements to full-run."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
            QPushButton, QVBoxLayout, QCheckBox,
            QScrollArea, QWidget,
        )

        dialog = QDialog(self.widget)
        dialog.setWindowTitle("Watershed Data")
        dialog.setMinimumWidth(680)
        dialog.resize(760, 720)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "Create a SiteSpec, validate it, or download one explicitly declared HTTPS "
            "provider product. Full Run to OHQ remains unchanged."
        ))
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        form_widget = QWidget(scroll)
        form_layout = QVBoxLayout(form_widget)
        form = QFormLayout()
        config_path = Path(self.config.text()).expanduser().resolve()
        try:
            project_config = _read_config(config_path) if config_path.is_file() else {}
        except (OSError, ValueError, yaml.YAMLError):
            project_config = {}
        if not isinstance(project_config, dict):
            project_config = {}
        project_dir = config_path.parent
        configured_site = _site_name(project_config)
        site_id = Path(configured_site).name.replace(" ", "_").lower() or "watershed"
        outlet = project_config.get("outlet", {})
        if not isinstance(outlet, dict):
            outlet = {}
        data_config = project_config.get("watershed_data", {})
        if not isinstance(data_config, dict):
            data_config = {}
        period = data_config.get("study_period", {})
        if not isinstance(period, dict):
            period = {}
        workspace = project_dir / "outputs" / f"{site_id}_data"
        defaults = {
            "SiteSpec": str(project_dir / "sites" / f"{site_id}.yaml"),
            "Site ID": site_id, "Name": Path(configured_site).name,
            "Longitude": str(outlet.get("longitude") or ""),
            "Latitude": str(outlet.get("latitude") or ""),
            "Start (UTC)": str(period.get("start") or ""),
            "End (UTC)": str(period.get("end") or ""), "Product URL": "", "Provider": "",
            "Product": "", "Product version": "unspecified",
            "Cache": str(workspace / "cache"),
            "Catalog": str(workspace / "watershed_package/catalog.json"),
            "Package": str(workspace / "watershed_package"), "Raw inclusion": "referenced",
            "Reconnaissance output": str(workspace / "reconnaissance"), "Gauge radius (km)": "50",
            "Selected USGS station ID": "",
            "Weather variables": "PRECTOTCORR,T2M,RH2M,WS2M,ALLSKY_SFC_SW_DWN",
            "Native asset ID": "",
            "Asset product": "historical-meteorology",
            "QC output": str(workspace / "watershed_package/quality_control/temporal.json"),
            "Provenance output": str(workspace / "watershed_package/provenance/temporal.json"),
            "HydroPINN output": str(workspace / "hydropinn"),
            "All-data workspace": str(workspace),
            "Forecast URL": "", "Forecast provider": "", "Forecast product": "forecast",
            "Prediction time (UTC)": "",
            "Status output": str(workspace / "watershed_package/status"),
        }
        fields = {label: QLineEdit(value) for label, value in defaults.items()}
        site_row = QHBoxLayout()
        site_row.addWidget(fields["SiteSpec"])
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: fields["SiteSpec"].setText(
            QFileDialog.getSaveFileName(dialog, "Site specification", fields["SiteSpec"].text(),
                                        "YAML (*.yaml *.yml);;JSON (*.json)")[0]
            or fields["SiteSpec"].text()
        ))
        site_row.addWidget(browse)
        form.addRow("SiteSpec", site_row)
        for label, field in fields.items():
            if label != "SiteSpec":
                form.addRow(label, field)
        refresh_box = QCheckBox("Refresh provider responses (ignore reusable cache)")
        form.addRow(refresh_box)
        form_layout.addLayout(form)
        buttons_widget = QWidget(form_widget)
        from qgis.PyQt.QtWidgets import QGridLayout
        buttons = QGridLayout(buttons_widget)

        def run(action: str) -> None:
            if action == "use-recon-selection":
                try:
                    station = _selected_station_from_reconnaissance(
                        fields["Reconnaissance output"].text()
                    )
                except QgisDockConfigError as exc:
                    self.log.append(str(exc))
                    return
                fields["Selected USGS station ID"].setText(station)
                self.log.append(f"Selected USGS station from reconnaissance: {station}")
                return
            if action == "use-catalog-asset":
                try:
                    asset_id = _select_catalog_asset(
                        fields["Catalog"].text(), fields["Asset product"].text()
                    )
                except QgisDockConfigError as exc:
                    self.log.append(str(exc))
                    return
                fields["Native asset ID"].setText(asset_id)
                self.log.append(f"Selected native catalog asset: {asset_id}")
                return
            try:
                longitude = float(fields["Longitude"].text()) if fields["Longitude"].text() else None
                latitude = float(fields["Latitude"].text()) if fields["Latitude"].text() else None
                argv = _command_for_watershed_data(
                    action, site_spec=fields["SiteSpec"].text(),
                    site_id=fields["Site ID"].text(), name=fields["Name"].text(),
                    longitude=longitude, latitude=latitude,
                    start=fields["Start (UTC)"].text(), end=fields["End (UTC)"].text(),
                    url=fields["Product URL"].text(), provider=fields["Provider"].text(),
                    product=fields["Product"].text(),
                    product_version=fields["Product version"].text(),
                    cache=fields["Cache"].text(), catalog=fields["Catalog"].text(),
                    package=fields["Package"].text(), include_raw=fields["Raw inclusion"].text(),
                    reconnaissance_output=fields["Reconnaissance output"].text(),
                    radius_km=float(fields["Gauge radius (km)"].text()),
                    station_id=fields["Selected USGS station ID"].text(),
                    weather_variables=fields["Weather variables"].text(),
                    asset_id=fields["Native asset ID"].text(),
                    qc_output=fields["QC output"].text(),
                    provenance_output=fields["Provenance output"].text(),
                    hydropinn_output=fields["HydroPINN output"].text(),
                    workspace=fields["All-data workspace"].text(),
                    forecast_url=fields["Forecast URL"].text(),
                    forecast_provider=fields["Forecast provider"].text(),
                    forecast_product=fields["Forecast product"].text(),
                    prediction_time=fields["Prediction time (UTC)"].text(),
                    status_output=fields["Status output"].text(),
                    refresh=refresh_box.isChecked(),
                )
            except (QgisDockConfigError, ValueError) as exc:
                self.log.append(f"Cannot run watershed data action: {exc}")
                return
            dialog.accept()
            self._start_argv(action, argv)

        actions = (
            ("Create SiteSpec", "init-site"), ("Validate SiteSpec", "validate-site"),
            ("Download Declared Product", "acquire-url"),
            ("Discover Discharge Gauges", "reconnaissance"),
            ("Use Reconnaissance Selection", "use-recon-selection"),
            ("Download Selected Discharge", "download-discharge"),
            ("Download Historical Weather", "download-weather"),
            ("Use Latest Native Asset", "use-catalog-asset"),
            ("Harmonize + QC", "harmonize"),
            ("Download PET/ET", "download-pet"),
            ("Freeze Package", "freeze"), ("Validate Package", "validate-package"),
            ("Export HydroPINN", "export-hydropinn"),
            ("RUN ALL DATA STEPS", "run"),
            ("RUN WEATHER/PET TO EXPORT", "run-weather"),
            ("Download Forecast Archive", "download-forecast"),
            ("Create Forecast View", "forecast-view"),
            ("Inspect Data Status", "status"),
            ("Check Data Workspace", "doctor"),
            ("Inspect Cache Garbage", "gc"),
        )
        for index, (label, action) in enumerate(actions):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=action: run(value))
            buttons.addWidget(button, index // 3, index % 3)
        form_layout.addWidget(buttons_widget)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll)
        dialog.exec_()

    def configure_documented_watershed(self) -> None:
        """Collect cited boundary settings without requiring manual YAML editing."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
            QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
        )

        config_path = Path(self.config.text()).expanduser()
        try:
            data = _read_config(config_path)
            if not isinstance(data, dict):
                raise QgisDockConfigError("Project config must contain an object.")
            current = _as_mapping(
                data.get("documented_watershed"), "documented_watershed"
            )
            fields = (
                ("source", "Local vector path or ArcGIS numeric layer URL"),
                ("layer", "Local container layer (optional)"),
                ("name_field", "Watershed name field (optional)"),
                ("name", "Exact watershed name (optional)"),
                ("title", "Reference dataset title"),
                ("organization", "Publishing organization"),
                ("url", "Citation URL (optional)"),
                ("license", "License/data terms (optional)"),
            )
            dialog = QDialog(self.widget)
            dialog.setWindowTitle("Documented Watershed Reference")
            dialog.setMinimumWidth(620)
            outer = QVBoxLayout(dialog)
            note = QLabel(
                "Use an agency polygon or ArcGIS numeric layer URL. "
                "Images and PDFs are evidence, not polygon inputs."
            )
            note.setWordWrap(True)
            outer.addWidget(note)
            form = QFormLayout()
            edits = {}
            for key, label in fields:
                edit = QLineEdit(str(current.get(key, "")))
                edits[key] = edit
                if key == "source":
                    source_row = QHBoxLayout()
                    source_row.addWidget(edit)
                    browse = QPushButton("Browse…")

                    def choose_source(source_edit=edit):
                        selected, _ = QFileDialog.getOpenFileName(
                            dialog, "Select documented watershed vector"
                        )
                        if selected:
                            source_edit.setText(selected)

                    browse.clicked.connect(choose_source)
                    source_row.addWidget(browse)
                    form.addRow(label, source_row)
                else:
                    form.addRow(label, edit)
            outer.addLayout(form)
            buttons = QDialogButtonBox(
                QDialogButtonBox.Save | QDialogButtonBox.Cancel
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            outer.addWidget(buttons)
            if dialog.exec_() != QDialog.Accepted:
                self.log.append("Documented watershed configuration cancelled.")
                return
            updated = {key: edit.text().strip() for key, edit in edits.items()}
            missing = [key for key in ("source", "title", "organization") if not updated[key]]
            if missing:
                raise QgisDockConfigError(
                    "Reference source, title, and organization are required."
                )
            data["documented_watershed"] = updated
            _write_config(config_path, data)
            self.log.append(
                "Saved documented watershed settings. Click Import Documented Watershed."
            )
        except (OSError, ValueError, QgisDockConfigError) as exc:
            self.log.append(f"Cannot configure documented watershed: {exc}")

    def set_outlet_coordinates(self) -> None:
        from qgis.PyQt.QtWidgets import QInputDialog

        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        outlet = data.get("outlet", {}) if isinstance(data, dict) else {}
        default_lon = float(outlet.get("longitude") or 0.0) if isinstance(outlet, dict) else 0.0
        default_lat = float(outlet.get("latitude") or 0.0) if isinstance(outlet, dict) else 0.0
        lon, accepted = QInputDialog.getDouble(
            self.widget,
            "Outlet Longitude",
            "Longitude (EPSG:4326)",
            default_lon,
            -180.0,
            180.0,
            8,
        )
        if not accepted:
            return
        lat, accepted = QInputDialog.getDouble(
            self.widget,
            "Outlet Latitude",
            "Latitude (EPSG:4326)",
            default_lat,
            -90.0,
            90.0,
            8,
        )
        if accepted:
            self.write_outlet(lon, lat)
            self.log.append(f"Outlet set to lon={lon:.8f}, lat={lat:.8f}")

    def _pour_point_path(self) -> Path:
        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        if not isinstance(data, dict):
            raise QgisDockConfigError("Project config must contain a JSON/YAML object.")
        root = _relative_to_config(config_path, data.get("root") or ".")
        if root is None:
            raise QgisDockConfigError("Project root could not be resolved.")
        return root / _site_name(data) / "outputs" / "pour_point_candidates.gpkg"

    def _pour_point_layer(self):
        """Return the editable review layer, creating it when Phase 1 has not."""
        from qgis.core import (
            QgsProject,
            QgsVectorFileWriter,
            QgsVectorLayer,
        )

        path = self._pour_point_path()
        source_prefix = str(path.resolve())
        for layer in QgsProject.instance().mapLayers().values():
            if (
                layer.source().split("|")[0] == source_prefix
                and layer.name() == "pour_point_candidates"
            ):
                return layer

        uri = f"{path}|layername=pour_point_candidates"
        layer = QgsVectorLayer(uri, "pour_point_candidates", "ogr")
        if not layer.isValid():
            path.parent.mkdir(parents=True, exist_ok=True)
            memory = QgsVectorLayer(
                "Point?crs=EPSG:4326"
                "&field=candidate_id:string(80)"
                "&field=reason:string(80)"
                "&field=review_status:string(20)",
                "pour_point_candidates",
                "memory",
            )
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = "pour_point_candidates"
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                memory, str(path), QgsProject.instance().transformContext(), options
            )
            if result[0] != QgsVectorFileWriter.NoError:
                raise QgisDockConfigError(
                    f"Could not create pour-point review layer {path}: {result}"
                )
            layer = QgsVectorLayer(uri, "pour_point_candidates", "ogr")
        if not layer.isValid():
            raise QgisDockConfigError(f"Could not open pour-point review layer: {path}")
        QgsProject.instance().addMapLayer(layer)
        return layer

    @staticmethod
    def _next_manual_candidate_id(layer) -> str:
        existing = {
            str(feature["candidate_id"])
            for feature in layer.getFeatures()
            if feature["candidate_id"] not in (None, "")
        }
        number = 1
        while f"manual-{number:03d}" in existing:
            number += 1
        return f"manual-{number:03d}"

    def _append_pour_point(self, point, source_crs) -> str:
        from qgis.core import (
            QgsCoordinateTransform,
            QgsFeature,
            QgsGeometry,
            QgsProject,
        )

        layer = self._pour_point_layer()
        transform = QgsCoordinateTransform(source_crs, layer.crs(), QgsProject.instance())
        layer_point = transform.transform(point)
        candidate_id = self._next_manual_candidate_id(layer)
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(layer_point))
        feature["candidate_id"] = candidate_id
        feature["reason"] = "manual_subwatershed_outlet"
        feature["review_status"] = "pending"
        if not layer.isEditable() and not layer.startEditing():
            raise QgisDockConfigError("Could not start editing pour-point review layer.")
        if not layer.addFeature(feature):
            raise QgisDockConfigError(f"Could not add pour-point candidate {candidate_id}.")
        layer.triggerRepaint()
        return candidate_id

    def pick_pour_points(self) -> None:
        try:
            self._pour_point_layer()
        except (OSError, QgisDockConfigError) as exc:
            self.log.append(f"ERROR: {exc}")
            return
        self.pour_point_tool = PourPointCaptureTool(self)
        self.pour_point_tool.activate()

    def add_pour_point_from_canvas(self, point) -> None:
        try:
            canvas = self.iface.mapCanvas()
            candidate_id = self._append_pour_point(
                point, canvas.mapSettings().destinationCrs()
            )
            self.log.append(f"Added pending pour point {candidate_id} from map canvas.")
        except (OSError, QgisDockConfigError) as exc:
            self.log.append(f"ERROR: {exc}")

    def add_pour_point_coordinates(self) -> None:
        from qgis.PyQt.QtWidgets import QInputDialog
        from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY

        lon, accepted = QInputDialog.getDouble(
            self.widget, "Pour Point Longitude", "Longitude (EPSG:4326)", decimals=8,
            min=-180.0, max=180.0
        )
        if not accepted:
            return
        lat, accepted = QInputDialog.getDouble(
            self.widget, "Pour Point Latitude", "Latitude (EPSG:4326)", decimals=8,
            min=-90.0, max=90.0
        )
        if not accepted:
            return
        try:
            candidate_id = self._append_pour_point(
                QgsPointXY(lon, lat), QgsCoordinateReferenceSystem("EPSG:4326")
            )
            self.log.append(
                f"Added pending pour point {candidate_id} at lon={lon:.8f}, lat={lat:.8f}."
            )
        except (OSError, QgisDockConfigError) as exc:
            self.log.append(f"ERROR: {exc}")

    def finish_pour_point_capture(self) -> None:
        try:
            layer = self._pour_point_layer()
            if layer.isEditable() and not layer.commitChanges():
                errors = "; ".join(layer.commitErrors())
                raise QgisDockConfigError(f"Could not save pour points: {errors}")
            self.log.append(
                "Saved pour-point candidates. Review reason/status attributes, set selected "
                "points to approved, then run Promote Reviewed Pour Points."
            )
            self._refresh_action_buttons()
        except (OSError, QgisDockConfigError) as exc:
            self.log.append(f"ERROR: {exc}")

    def draw_acquisition_polygon(self) -> None:
        self.polygon_tool = AcquisitionPolygonTool(self)
        self.polygon_tool.activate()

    def write_outlet(self, lon: float, lat: float) -> None:
        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        if not isinstance(data, dict):
            data = {}
        outlet = data.setdefault("outlet", {})
        if not isinstance(outlet, dict):
            outlet = {}
            data["outlet"] = outlet
        outlet["longitude"] = lon
        outlet["latitude"] = lat
        outlet.setdefault("input_crs", "EPSG:4326")
        _write_config(config_path, data)

    def write_acquisition_polygon(self, coords: list[tuple[float, float]], source: str) -> Path:
        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        if not isinstance(data, dict):
            data = {}
        dem = data.setdefault("dem_acquisition", {})
        if not isinstance(dem, dict):
            dem = {}
            data["dem_acquisition"] = dem
        area_value = dem.get("acquisition_area") or "intermediate/dem_acquisition_area.geojson"
        area_path = Path(area_value).expanduser()
        if not area_path.is_absolute():
            area_path = config_path.parent / area_path
        _write_geojson_polygon(area_path, coords, source=source)
        dem["method"] = "polygon"
        dem["acquisition_area"] = str(area_path if area_path.is_absolute() else area_value)
        _write_config(config_path, data)
        return area_path

    def use_canvas_extent_as_area(self) -> None:
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        source_crs = canvas.mapSettings().destinationCrs()
        target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        corners = [
            transform.transform(extent.xMinimum(), extent.yMinimum()),
            transform.transform(extent.xMaximum(), extent.yMinimum()),
            transform.transform(extent.xMaximum(), extent.yMaximum()),
            transform.transform(extent.xMinimum(), extent.yMaximum()),
        ]
        coords = [(point.x(), point.y()) for point in corners]
        coords.append(coords[0])
        area_path = self.write_acquisition_polygon(coords, "qgis_canvas_extent")
        self.log.append(f"Wrote DEM acquisition area from canvas extent: {area_path}")

    def run_command(self, command: str) -> None:
        from qgis.PyQt.QtCore import QProcess

        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.log.append("A workflow command is already running; wait for it to finish first.")
            return
        if command == "promote-pour-points":
            try:
                if not self._pour_point_path().is_file():
                    self.log.append(
                        "No pour-point candidate file exists. Generate candidates first, "
                        "review them in QGIS, then promote them."
                    )
                    return
            except (OSError, QgisDockConfigError) as exc:
                self.log.append(f"Cannot promote pour points: {exc}")
                return
        try:
            skip_prepare = bool(getattr(self, "skip_full_prepare_once", False))
            self.skip_full_prepare_once = False
            if command == "full-run" and not skip_prepare:
                data = _read_config(Path(self.config.text()).expanduser())
                dem = data.get("dem_acquisition", {}) if isinstance(data, dict) else {}
                if isinstance(dem, dict) and dem.get("method") in {
                    "outlet_buffer", "oriented_outlet_buffer", "upstream_network"
                }:
                    self.pending_workflow = "full-run"
                    command = "prepare-dem"
                    self.log.append(
                        "Generating the configured default acquisition area before FULL RUN."
                    )
            argv = _command_for_workflow(
                command,
                self.config.text(),
                use_reviewed_pour_points=self.reviewed_points.isChecked(),
                nhdplus_snap_distance_m=self.nhdplus_snap_distance.value(),
                minimum_watershed_area_km2=self.minimum_watershed_area.value(),
                minimum_subwatershed_area_km2=self.minimum_subwatershed_area.value(),
                minimum_area_ratio=self.minimum_area_ratio.value(),
                maximum_area_ratio=self.maximum_area_ratio.value(),
                overwrite_promoted_pour_points=self.overwrite_promoted.isChecked(),
                use_existing_outlet=self.use_existing_outlet.isChecked(),
                reuse_downloads=self.reuse_downloads.isChecked(),
            )
        except (OSError, QgisDockConfigError, ValueError) as exc:
            self.log.append(f"Cannot run {command}: {exc}")
            return
        self._start_argv(command, argv)

    def _start_argv(self, command: str, argv: list[str]) -> None:
        from qgis.PyQt.QtCore import QProcess

        if self.process is not None and self.process.state() != QProcess.NotRunning:
            self.log.append("A workflow command is already running; wait for it to finish first.")
            return
        self.log.append("$ " + " ".join(argv))
        self.process = QProcess(self.widget)
        self.process.readyReadStandardOutput.connect(self._append_process_stdout)
        self.process.readyReadStandardError.connect(self._append_process_stderr)
        self.process.finished.connect(
            lambda code, status, value=command: self._command_finished(value, code, status)
        )
        self.process.start(argv[0], argv[1:])

    def _append_process_stdout(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if text:
            self.log.append(text.rstrip())

    def _append_process_stderr(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        if text:
            self.log.append(text.rstrip())

    def _command_finished(self, command: str, code: int, status) -> None:
        self.log.append(f"[{command} exited with {code}]")
        self.process = None
        self._refresh_action_buttons()
        pending = getattr(self, "pending_workflow", None)
        self.pending_workflow = None
        if pending and code == 0:
            self.skip_full_prepare_once = True
            self.run_command(pending)
        if command == "import-watershed-reference" and code == 0:
            self.load_configured_layers()

    def _refresh_action_buttons(self) -> None:
        button = getattr(self, "action_buttons", {}).get("promote-pour-points")
        if button is None:
            return
        try:
            button.setEnabled(self._pour_point_path().is_file())
        except (OSError, QgisDockConfigError):
            button.setEnabled(False)

    def load_configured_layers(self) -> None:
        from qgis.core import QgsProject, QgsVectorLayer

        config_path = Path(self.config.text()).expanduser()
        data = _read_config(config_path)
        dem = data.get("dem_acquisition", {}) if isinstance(data, dict) else {}
        outlet = data.get("outlet", {}) if isinstance(data, dict) else {}
        layer_values = {
            key: dem.get(key)
            for key in (
                "acquisition_area",
                "expanded_acquisition_area",
                "watershed_boundary",
                "tile_index",
            )
        }
        try:
            project_root = _relative_to_config(config_path, data.get("root") or ".")
            documented = (
                project_root / _site_name(data) / "outputs"
                / "DocumentedWatershed_reference.gpkg"
            )
            if documented.is_file():
                layer_values["documented_watershed_reference"] = str(documented)
        except (TypeError, ValueError):
            pass
        if isinstance(outlet, dict):
            layer_values.update(
                {
                    "outlet_raw": outlet.get("raw_path"),
                    "outlet_snapped": outlet.get("snapped_path"),
                }
            )
        manifest_value = dem.get("tile_manifest")
        if manifest_value:
            manifest_path = Path(manifest_value).expanduser()
            if not manifest_path.is_absolute():
                manifest_path = config_path.parent / manifest_path
            if manifest_path.exists():
                footprint_path = _write_manifest_footprints(manifest_path)
                if footprint_path is not None:
                    layer_values["selected_tile_footprints"] = str(footprint_path)
        for key, value in layer_values.items():
            if not value:
                continue
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = config_path.parent / path
            if path.exists():
                layer = QgsVectorLayer(str(path), key, "ogr")
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.log.append(f"Loaded layer: {path}")

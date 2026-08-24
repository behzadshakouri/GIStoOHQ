from __future__ import annotations

from pathlib import Path
from typing import Callable

from .catalog import AssetCatalog
from .forecast import acquire_forecast_archive, materialize_available_forecasts
from .hydropinn import export_hydropinn
from .nasa_power import acquire_historical_meteorology, acquire_pet_et
from .package import freeze_package, validate_package
from .schemas import SiteSpec, WatershedDataError
from .temporal import harmonize_asset
from .usgs import acquire_observed_discharge
from .workflow import write_site_spec


def run_watershed_data_pipeline(
    *,
    site_spec: str | Path,
    station_id: str,
    workspace: str | Path,
    include_discharge: bool = True,
    include_weather: bool = True,
    include_pet: bool = True,
    export_hydropinn_profile: bool = False,
    discharge_acquirer: Callable = acquire_observed_discharge,
    weather_acquirer: Callable = acquire_historical_meteorology,
    pet_acquirer: Callable = acquire_pet_et,
    forecast_acquirer: Callable = acquire_forecast_archive,
    forecast_view_builder: Callable = materialize_available_forecasts,
    forecast_url: str = "",
    forecast_provider: str = "",
    forecast_product: str = "forecast",
    prediction_time: str = "",
    refresh: bool = False,
    init_if_missing: bool = False,
    site_id: str = "",
    name: str | None = None,
    longitude: float | None = None,
    latitude: float | None = None,
    study_start: str = "",
    study_end: str = "",
) -> dict[str, object]:
    """Run the optional native→QC→package workflow after explicit gauge selection."""
    if include_discharge and not station_id:
        raise WatershedDataError("an explicit station ID is required when discharge is enabled")
    if not any((include_discharge, include_weather, include_pet)) and not forecast_url:
        raise WatershedDataError("select at least one watershed-data product")
    if bool(forecast_url) != bool(forecast_provider):
        raise WatershedDataError("forecast URL and provider must be supplied together")
    if prediction_time and not forecast_url:
        raise WatershedDataError("prediction time requires a forecast URL and provider")
    site_path = Path(site_spec).expanduser().resolve()
    if not site_path.exists() and init_if_missing:
        missing = [
            label for label, value in {
                "site_id": site_id, "longitude": longitude, "latitude": latitude,
                "study_start": study_start, "study_end": study_end,
            }.items() if value in (None, "")
        ]
        if missing:
            raise WatershedDataError(
                "cannot initialize missing SiteSpec; required values: " + ", ".join(missing)
            )
        write_site_spec(
            site_path, site_id=site_id, name=name, longitude=float(longitude),
            latitude=float(latitude), start=study_start, end=study_end,
        )
    spec = SiteSpec.from_file(site_path)
    root = Path(workspace).expanduser().resolve()
    cache = root / "cache"
    package = root / "watershed_package"
    catalog = package / "catalog.json"
    qc_dir = package / "quality_control"
    provenance_dir = package / "provenance"
    native_assets = []
    if include_discharge:
        native_assets.append(discharge_acquirer(
            spec, station_id, cache=cache, catalog=catalog, refresh=refresh
        ))
    if include_weather:
        native_assets.append(weather_acquirer(spec, cache=cache, catalog=catalog, refresh=refresh))
    if include_pet:
        native_assets.append(pet_acquirer(spec, cache=cache, catalog=catalog, refresh=refresh))
    forecast_asset = None
    forecast_view = None
    if forecast_url:
        forecast_asset = forecast_acquirer(
            url=forecast_url, provider=forecast_provider, product=forecast_product,
            cache=cache, catalog=catalog,
            refresh=refresh,
        )
        native_assets.append(forecast_asset)
    derived_assets = []
    for asset in native_assets:
        if asset is forecast_asset:
            continue
        safe_id = asset["asset_id"].replace(":", "_")
        derived_assets.append(harmonize_asset(
            asset_id=asset["asset_id"], catalog=catalog, object_store=cache,
            qc_output=qc_dir / f"{safe_id}.json",
            provenance_output=provenance_dir / f"{safe_id}.json",
            expected_start=spec.study_start, expected_end=spec.study_end,
            fail_on_qc_error=True,
        ))
    if forecast_asset is not None and prediction_time:
        forecast_view = forecast_view_builder(
            asset_id=forecast_asset["asset_id"], prediction_time=prediction_time,
            object_store=cache, catalog=catalog,
        )
        derived_assets.append(forecast_view)
    manifest_path = freeze_package(
        site_spec=site_path, catalog=catalog, output=package,
        include_raw="referenced", object_store=cache,
    )
    manifest = validate_package(package)
    hydropinn_manifest = None
    if export_hydropinn_profile:
        if manifest.package_qc_status == "fail":
            raise WatershedDataError(
                "HydroPINN export refused because the watershed package has failed QC: "
                + ", ".join(manifest.failed_qc_rule_ids)
            )
        hydropinn_manifest = export_hydropinn(
            package=package, object_store=cache, output=root / "hydropinn"
        )
    return {
        "site_id": spec.site_id,
        "workspace": str(root),
        "catalog": str(AssetCatalog(catalog).path),
        "native_asset_ids": [asset["asset_id"] for asset in native_assets],
        "derived_asset_ids": [asset["asset_id"] for asset in derived_assets],
        "package_manifest": str(manifest_path),
        "package_id": manifest.package_id,
        "package_qc_status": manifest.package_qc_status,
        "failed_qc_rule_ids": list(manifest.failed_qc_rule_ids),
        "hydropinn_manifest": str(hydropinn_manifest) if hydropinn_manifest else None,
        "forecast_asset_id": forecast_asset["asset_id"] if forecast_asset else None,
        "forecast_view_asset_id": forecast_view["asset_id"] if forecast_view else None,
    }

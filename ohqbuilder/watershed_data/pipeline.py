from __future__ import annotations

from pathlib import Path
from typing import Callable

from .catalog import AssetCatalog
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
    if not any((include_discharge, include_weather, include_pet)):
        raise WatershedDataError("select at least one watershed-data product")
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
        native_assets.append(discharge_acquirer(spec, station_id, cache=cache, catalog=catalog))
    if include_weather:
        native_assets.append(weather_acquirer(spec, cache=cache, catalog=catalog))
    if include_pet:
        native_assets.append(pet_acquirer(spec, cache=cache, catalog=catalog))
    derived_assets = []
    for asset in native_assets:
        safe_id = asset["asset_id"].replace(":", "_")
        derived_assets.append(harmonize_asset(
            asset_id=asset["asset_id"], catalog=catalog, object_store=cache,
            qc_output=qc_dir / f"{safe_id}.json",
            provenance_output=provenance_dir / f"{safe_id}.json",
        ))
    manifest_path = freeze_package(
        site_spec=site_path, catalog=catalog, output=package,
        include_raw="referenced", object_store=cache,
    )
    manifest = validate_package(package)
    hydropinn_manifest = None
    if export_hydropinn_profile:
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
        "hydropinn_manifest": str(hydropinn_manifest) if hydropinn_manifest else None,
    }

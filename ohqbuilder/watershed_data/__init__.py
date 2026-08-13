"""Provider-neutral watershed data acquisition foundations."""

from .catalog import AssetCatalog, ObjectStore
from .package import freeze_package, validate_package
from .pipeline import run_watershed_data_pipeline
from .hydropinn import export_hydropinn
from .forecast import acquire_forecast_archive, materialize_available_forecasts
from .nasa_power import acquire_historical_meteorology, acquire_pet_et
from .reconnaissance import run_reconnaissance
from .usgs import acquire_observed_discharge
from .temporal import harmonize_asset, temporal_qc
from .status import build_data_status, write_data_status
from .schemas import (
    PackageManifest,
    ProvenanceActivity,
    QCResult,
    SiteSpec,
    WatershedDataError,
    canonical_request_key,
)

__all__ = [
    "AssetCatalog",
    "ObjectStore",
    "PackageManifest",
    "ProvenanceActivity",
    "QCResult",
    "SiteSpec",
    "WatershedDataError",
    "canonical_request_key",
    "acquire_observed_discharge",
    "acquire_historical_meteorology",
    "acquire_pet_et",
    "export_hydropinn",
    "acquire_forecast_archive",
    "materialize_available_forecasts",
    "freeze_package",
    "run_reconnaissance",
    "run_watershed_data_pipeline",
    "validate_package",
    "harmonize_asset",
    "temporal_qc",
    "build_data_status",
    "write_data_status",
]

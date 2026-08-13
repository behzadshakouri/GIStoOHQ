"""Provider-neutral watershed data acquisition foundations."""

from .catalog import AssetCatalog, ObjectStore
from .package import freeze_package, validate_package
from .hydropinn import export_hydropinn
from .nasa_power import acquire_historical_meteorology, acquire_pet_et
from .reconnaissance import run_reconnaissance
from .usgs import acquire_observed_discharge
from .temporal import harmonize_asset, temporal_qc
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
    "freeze_package",
    "run_reconnaissance",
    "validate_package",
    "harmonize_asset",
    "temporal_qc",
]

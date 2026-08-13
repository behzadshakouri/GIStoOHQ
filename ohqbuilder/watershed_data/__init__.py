"""Provider-neutral watershed data acquisition foundations."""

from .catalog import AssetCatalog, ObjectStore
from .package import freeze_package, validate_package
from .reconnaissance import run_reconnaissance
from .usgs import acquire_observed_discharge
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
    "freeze_package",
    "run_reconnaissance",
    "validate_package",
]

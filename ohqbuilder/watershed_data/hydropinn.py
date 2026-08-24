from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .package import validate_package
from .schemas import WatershedDataError


def export_hydropinn(
    *, package: str | Path, object_store: str | Path | None,
    output: str | Path, profile: str = "water-balance-v1",
) -> Path:
    """Export selected generic temporal assets without ML preprocessing."""
    if profile != "water-balance-v1":
        raise WatershedDataError(f"unsupported HydroPINN profile: {profile}")
    root = Path(package).expanduser().resolve()
    package_manifest = validate_package(root)
    if package_manifest.package_qc_status == "fail":
        raise WatershedDataError(
            "HydroPINN export refused because the source package has failed QC"
        )
    catalog = AssetCatalog(root / "catalog.json").read()
    assets = [
        asset for asset in catalog["assets"]
        if asset.get("processing_status") == "derived"
        and asset.get("media_type") == "text/csv"
        and asset.get("product") == "harmonized-temporal-observations"
    ]
    if not assets:
        raise WatershedDataError("package has no harmonized temporal assets to export")
    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    observations = destination / "observations"
    observations.mkdir(exist_ok=True)
    variable_units: dict[str, set[str]] = {}
    exported = []
    for index, asset in enumerate(sorted(assets, key=lambda item: item["asset_id"])):
        digest = asset["content_digest"]
        packaged = root / "raw" / "sha256" / digest[:2] / digest[2:]
        if packaged.is_file():
            source = packaged.open("rb")
        elif object_store is not None:
            source = ObjectStore(object_store).open(digest)
        else:
            raise WatershedDataError("referenced package export requires --object-store")
        target = observations / f"temporal_{index + 1}.csv"
        with source, target.open("wb") as sink:
            shutil.copyfileobj(source, sink)
        with target.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                variable_units.setdefault(row["variable"], set()).add(row["native_unit"])
        exported.append({
            "asset_id": asset["asset_id"], "path": str(target.relative_to(destination)),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })
    variables = {
        "schema_name": "HydroPINNVariables", "schema_version": "1.0",
        "variables": [
            {"name": name, "units": sorted(units), "normalization": None,
             "missing_values": "preserved"}
            for name, units in sorted(variable_units.items())
        ],
    }
    _atomic_json(destination / "variables.json", variables)
    manifest: dict[str, Any] = {
        "schema_name": "HydroPINNExport", "schema_version": "1.0",
        "profile": profile, "source_package_id": package_manifest.package_id,
        "site_id": package_manifest.site_id, "assets": exported,
        "transformations_not_performed": [
            "normalization", "imputation", "lag_construction", "feature_selection",
            "train_validation_test_partitioning",
        ],
    }
    _atomic_json(destination / "manifest.json", manifest)
    return destination / "manifest.json"

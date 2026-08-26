from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, ObjectStore
from .package import validate_package
from .schemas import SiteSpec, WatershedDataError


def run_data_doctor(
    *, site_spec: str | Path, catalog: str | Path | None = None,
    object_store: str | Path | None = None, package: str | Path | None = None,
) -> dict[str, Any]:
    """Check a local watershed-data workspace without contacting providers."""
    checks = []
    spec_digest: str | None = None
    catalog_digest: str | None = None

    def record(name: str, passed: bool, message: str) -> None:
        checks.append({"name": name, "passed": passed, "message": message})

    try:
        spec = SiteSpec.from_file(site_spec)
        spec_digest = spec.digest
        record("site_spec", True, f"valid SiteSpec for {spec.site_id}")
    except WatershedDataError as exc:
        record("site_spec", False, str(exc))

    if catalog is not None:
        try:
            catalog_path = Path(catalog).expanduser().resolve()
            if not catalog_path.is_file():
                raise WatershedDataError(f"asset catalog does not exist: {catalog_path}")
            data = AssetCatalog(catalog_path).read()
            catalog_digest = data.get("catalog_digest")
            if not isinstance(catalog_digest, str):
                raise WatershedDataError("asset catalog is missing catalog_digest")
            record("catalog", True, f"catalog contains {len(data['assets'])} assets")
            if object_store is not None:
                store = ObjectStore(object_store)
                corrupt = []
                for asset in data["assets"]:
                    digest = asset["content_digest"]
                    try:
                        with store.open(digest) as stream:
                            hasher = hashlib.sha256()
                            while chunk := stream.read(1024 * 1024):
                                hasher.update(chunk)
                            actual = hasher.hexdigest()
                        if actual != digest:
                            corrupt.append(asset["asset_id"])
                    except OSError:
                        corrupt.append(asset["asset_id"])
                record(
                    "object_store", not corrupt,
                    "all catalog objects are present and valid" if not corrupt
                    else f"{len(corrupt)} catalog objects are missing or corrupt",
                )
        except WatershedDataError as exc:
            record("catalog", False, str(exc))

    if package is not None:
        try:
            manifest = validate_package(package)
            record("package", True, f"valid package {manifest.package_id}")
            input_mismatches = []
            if spec_digest is not None and manifest.site_spec_digest != spec_digest:
                input_mismatches.append("SiteSpec")
            if catalog_digest is not None and manifest.catalog_digest != catalog_digest:
                input_mismatches.append("catalog")
            record(
                "package_inputs", not input_mismatches,
                "package matches the supplied SiteSpec and catalog"
                if not input_mismatches else
                "package does not match supplied " + " and ".join(input_mismatches),
            )
            record(
                "package_qc", manifest.package_qc_status != "fail",
                f"package QC status is {manifest.package_qc_status}"
                + (f" ({', '.join(manifest.failed_qc_rule_ids)})"
                   if manifest.failed_qc_rule_ids else ""),
            )
        except WatershedDataError as exc:
            record("package", False, str(exc))

    return {
        "schema_name": "WatershedDataDoctor", "schema_version": "1.0",
        "passed": all(check["passed"] for check in checks), "checks": checks,
    }

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

    def record(name: str, passed: bool, message: str) -> None:
        checks.append({"name": name, "passed": passed, "message": message})

    try:
        spec = SiteSpec.from_file(site_spec)
        record("site_spec", True, f"valid SiteSpec for {spec.site_id}")
    except WatershedDataError as exc:
        record("site_spec", False, str(exc))

    if catalog is not None:
        try:
            data = AssetCatalog(catalog).read()
            record("catalog", True, f"catalog contains {len(data['assets'])} assets")
            if object_store is not None:
                store = ObjectStore(object_store)
                corrupt = []
                for asset in data["assets"]:
                    digest = asset["content_digest"]
                    try:
                        with store.open(digest) as stream:
                            actual = hashlib.sha256(stream.read()).hexdigest()
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
        except WatershedDataError as exc:
            record("package", False, str(exc))

    return {
        "schema_name": "WatershedDataDoctor", "schema_version": "1.0",
        "passed": all(check["passed"] for check in checks), "checks": checks,
    }

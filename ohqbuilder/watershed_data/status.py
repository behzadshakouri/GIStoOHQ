from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .package import validate_package
from .schemas import WatershedDataError


def build_data_status(
    *, catalog: str | Path, object_store: str | Path | None = None,
    package: str | Path | None = None,
) -> dict[str, Any]:
    """Return a stable summary plus every asset ID needed by later commands."""
    data = AssetCatalog(catalog).read()
    store = ObjectStore(object_store) if object_store is not None else None
    assets = []
    missing_objects = 0
    for asset in data["assets"]:
        digest = asset.get("content_digest", "")
        available = None
        if store is not None and digest:
            try:
                with store.open(digest):
                    available = True
            except OSError:
                available = False
                missing_objects += 1
        assets.append({
            "asset_id": asset.get("asset_id"),
            "provider": asset.get("provider"),
            "product": asset.get("product") or "unknown",
            "processing_status": asset.get("processing_status", "unknown"),
            "media_type": asset.get("media_type"),
            "content_digest": digest,
            "object_available": available,
            "parent_asset_ids": asset.get("parent_asset_ids", []),
            "temporal_coverage": asset.get("temporal_coverage"),
            "issue_time_coverage": asset.get("issue_time_coverage"),
            "valid_time_coverage": asset.get("valid_time_coverage"),
            "prediction_time": asset.get("prediction_time"),
            "variables": asset.get("variables", []),
            "members_by_variable": asset.get("members_by_variable", {}),
            "locations_by_variable": asset.get("locations_by_variable", {}),
            "units_by_variable": asset.get("units_by_variable", {}),
            "record_counts_by_variable": asset.get("record_counts_by_variable", {}),
            "record_count": asset.get("record_count", asset.get("observation_count")),
            "acquisition_attempts": asset.get("acquisition_attempts"),
        })
    package_manifest = validate_package(package) if package is not None else None
    if (package_manifest is not None
            and package_manifest.catalog_digest != data.get("catalog_digest")):
        raise WatershedDataError("status catalog does not match the validated package catalog")
    return {
        "schema_name": "WatershedDataStatus",
        "schema_version": "1.1",
        "catalog": str(Path(catalog).expanduser().resolve()),
        "catalog_digest": data.get("catalog_digest"),
        "asset_count": len(assets),
        "native_asset_count": sum(item["processing_status"] == "native" for item in assets),
        "derived_asset_count": sum(item["processing_status"] == "derived" for item in assets),
        "missing_object_count": missing_objects,
        "products": dict(sorted(Counter(item["product"] for item in assets).items())),
        "package_id": package_manifest.package_id if package_manifest else None,
        "package_qc_status": package_manifest.package_qc_status if package_manifest else None,
        "failed_qc_rule_ids": list(package_manifest.failed_qc_rule_ids) if package_manifest else [],
        "qc_policy_digests": dict(package_manifest.qc_policy_digests) if package_manifest else {},
        "validation_policy_digests": (
            dict(package_manifest.validation_policy_digests) if package_manifest else {}
        ),
        "assets": assets,
    }


def write_data_status(
    *, catalog: str | Path, output: str | Path,
    object_store: str | Path | None = None,
    package: str | Path | None = None,
) -> Path:
    report = build_data_status(catalog=catalog, object_store=object_store, package=package)
    destination = Path(output).expanduser().resolve()
    if destination.suffix.lower() == ".json":
        json_path = destination
        markdown_path = destination.with_suffix(".md")
    else:
        destination.mkdir(parents=True, exist_ok=True)
        json_path, markdown_path = destination / "status.json", destination / "status.md"
    _atomic_json(json_path, report)
    rows = [
        "# Watershed data status", "",
        f"Assets: **{report['asset_count']}** "
        f"({report['native_asset_count']} native, {report['derived_asset_count']} derived)", "",
        "| Asset ID | Provider | Product | Status | Attempts | Object available |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if report["package_id"] is not None:
        failed_rules = ", ".join(report["failed_qc_rule_ids"]) or "none"
        rows[4:4] = [
            f"Package: `{report['package_id']}`", "",
            f"Package QC: **{report['package_qc_status']}** (failed rules: {failed_rules})", "",
        ]
    for asset in report["assets"]:
        available = "not checked" if asset["object_available"] is None else (
            "yes" if asset["object_available"] else "no"
        )
        rows.append(
            f"| `{asset['asset_id']}` | {asset['provider']} | {asset['product']} | "
            f"{asset['processing_status']} | {asset['acquisition_attempts'] or '—'} | {available} |"
        )
    forecast_assets = [
        asset for asset in report["assets"]
        if asset["issue_time_coverage"] is not None or asset["valid_time_coverage"] is not None
    ]
    if forecast_assets:
        rows.extend([
            "", "## Forecast support", "",
            "| Asset ID | Prediction time | Issue coverage | Valid coverage | Variables |",
            "| --- | --- | --- | --- | --- |",
        ])
        for asset in forecast_assets:
            issue = asset["issue_time_coverage"] or {}
            valid = asset["valid_time_coverage"] or {}
            issue_text = f"{issue.get('start', '—')} → {issue.get('end', '—')}"
            valid_text = f"{valid.get('start', '—')} → {valid.get('end', '—')}"
            rows.append(
                f"| `{asset['asset_id']}` | {asset['prediction_time'] or '—'} | "
                f"{issue_text} | {valid_text} | {', '.join(asset['variables']) or '—'} |"
            )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return json_path

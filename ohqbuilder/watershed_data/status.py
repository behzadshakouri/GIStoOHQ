from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, ObjectStore, _atomic_json


def build_data_status(
    *, catalog: str | Path, object_store: str | Path | None = None,
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
            "record_count": asset.get("record_count", asset.get("observation_count")),
        })
    return {
        "schema_name": "WatershedDataStatus",
        "schema_version": "1.0",
        "catalog": str(Path(catalog).expanduser().resolve()),
        "catalog_digest": data.get("catalog_digest"),
        "asset_count": len(assets),
        "native_asset_count": sum(item["processing_status"] == "native" for item in assets),
        "derived_asset_count": sum(item["processing_status"] == "derived" for item in assets),
        "missing_object_count": missing_objects,
        "products": dict(sorted(Counter(item["product"] for item in assets).items())),
        "assets": assets,
    }


def write_data_status(
    *, catalog: str | Path, output: str | Path,
    object_store: str | Path | None = None,
) -> Path:
    report = build_data_status(catalog=catalog, object_store=object_store)
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
        "| Asset ID | Provider | Product | Status | Object available |",
        "| --- | --- | --- | --- | --- |",
    ]
    for asset in report["assets"]:
        available = "not checked" if asset["object_available"] is None else (
            "yes" if asset["object_available"] else "no"
        )
        rows.append(
            f"| `{asset['asset_id']}` | {asset['provider']} | {asset['product']} | "
            f"{asset['processing_status']} | {available} |"
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return json_path

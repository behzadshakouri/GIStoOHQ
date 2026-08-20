from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .schemas import WatershedDataError


def collect_unreferenced_objects(
    *, object_store: str | Path, catalogs: list[str | Path], delete: bool = False,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Report, and optionally remove, objects not referenced by supplied catalogs."""
    if not catalogs:
        raise WatershedDataError("garbage collection requires at least one asset catalog")
    referenced = {
        asset["content_digest"]
        for catalog in catalogs for asset in AssetCatalog(catalog).read()["assets"]
    }
    store = ObjectStore(object_store)
    object_root = store.root / "objects" / "sha256"
    candidates = []
    if object_root.exists():
        for path in sorted(item for item in object_root.rglob("*") if item.is_file()):
            digest = "".join(path.relative_to(object_root).parts)
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                continue
            if digest not in referenced:
                candidates.append({"content_digest": digest, "size": path.stat().st_size})
                if delete:
                    path.unlink()
    report = {
        "schema_name": "ObjectStoreGarbageCollection", "schema_version": "1.0",
        "object_store": str(store.root),
        "catalogs": [str(Path(path).expanduser().resolve()) for path in catalogs],
        "delete_requested": delete, "candidate_count": len(candidates),
        "candidate_bytes": sum(item["size"] for item in candidates),
        "removed_count": len(candidates) if delete else 0, "objects": candidates,
    }
    if output is not None:
        _atomic_json(Path(output).expanduser().resolve(), report)
    return report

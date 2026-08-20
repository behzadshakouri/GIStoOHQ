from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from .schemas import WatershedDataError, canonical_json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class _CatalogLock:
    """Small cross-platform lock based on exclusive lock-file creation."""

    def __init__(self, path: Path, *, timeout: float = 10.0):
        self.path = path.with_suffix(path.suffix + ".lock")
        self.timeout = timeout
        self.descriptor: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.descriptor, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if self._reclaim_dead_owner():
                    continue
                if time.monotonic() >= deadline:
                    raise WatershedDataError(f"timed out waiting for catalog lock: {self.path}")
                time.sleep(0.05)

    def _reclaim_dead_owner(self) -> bool:
        """Remove an orphaned local lock without unlinking a replacement lock."""
        try:
            before = self.path.stat()
            owner = int(self.path.read_text(encoding="ascii").strip())
        except (FileNotFoundError, OSError, UnicodeError, ValueError):
            return False
        try:
            os.kill(owner, 0)
            return False
        except PermissionError:
            return False
        except ProcessLookupError:
            pass
        try:
            current = self.path.stat()
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                return False
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True

    def __exit__(self, exc_type, exc_value, traceback):
        if self.descriptor is not None:
            os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True)
class StoredObject:
    content_digest: str
    path: Path
    size: int


class ObjectStore:
    """Immutable SHA-256 object store with atomic publication."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def put(self, source: BinaryIO) -> StoredObject:
        staging = self.root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="object.", dir=staging)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
                    size += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            content_digest = digest.hexdigest()
            destination = self.root / "objects" / "sha256" / content_digest[:2] / content_digest[2:]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                existing_digest = hashlib.sha256()
                existing_size = 0
                with destination.open("rb") as existing:
                    while chunk := existing.read(1024 * 1024):
                        existing_digest.update(chunk)
                        existing_size += len(chunk)
                if existing_digest.hexdigest() != content_digest or existing_size != size:
                    raise WatershedDataError(
                        f"immutable object is corrupt and will not be overwritten: {destination}"
                    )
                os.unlink(temporary)
            else:
                os.replace(temporary, destination)
            return StoredObject(content_digest, destination, size)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def open(self, digest: str) -> BinaryIO:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise WatershedDataError("content digest must be a lowercase SHA-256 value")
        return (self.root / "objects" / "sha256" / digest[:2] / digest[2:]).open("rb")


class AssetCatalog:
    """Append-only catalog snapshot for native and derived assets."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_name": "AssetCatalog", "schema_version": "1.0", "assets": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WatershedDataError(f"could not read asset catalog: {exc}") from exc
        if not isinstance(data.get("assets"), list):
            raise WatershedDataError("asset catalog assets must be an array")
        return data

    def register(self, asset: dict[str, Any]) -> dict[str, Any]:
        required = {"provider", "product", "content_digest", "size", "media_type"}
        missing = sorted(required - asset.keys())
        if missing:
            raise WatershedDataError(f"asset is missing required fields: {', '.join(missing)}")
        digest = asset["content_digest"]
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise WatershedDataError("asset content_digest must be a lowercase SHA-256 value")
        size = asset["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise WatershedDataError("asset size must be a non-negative integer")
        if not isinstance(asset["provider"], str) or not asset["provider"].strip():
            raise WatershedDataError("asset provider must be a non-empty string")
        if not isinstance(asset["product"], str) or not asset["product"].strip():
            raise WatershedDataError("asset product must be a non-empty string")
        if not isinstance(asset["media_type"], str) or "/" not in asset["media_type"]:
            raise WatershedDataError("asset media_type must be a valid type/subtype string")
        if asset.get("processing_status") == "derived":
            parents = asset.get("parent_asset_ids")
            if not isinstance(parents, list) or not parents or any(
                not isinstance(value, str) or not value for value in parents
            ):
                raise WatershedDataError("derived asset requires non-empty parent_asset_ids")
            if not isinstance(asset.get("transformation_name"), str) or not asset[
                "transformation_name"
            ].strip():
                raise WatershedDataError("derived asset requires transformation_name")
            if not isinstance(asset.get("transformation_version"), str) or not asset[
                "transformation_version"
            ].strip():
                raise WatershedDataError("derived asset requires transformation_version")
            if not isinstance(asset.get("transformation_parameters"), dict):
                raise WatershedDataError("derived asset requires transformation_parameters")
        record = dict(asset)
        identity = {
            key: record.get(key)
            for key in ("provider", "product", "product_version", "request_key", "content_digest")
        }
        record.setdefault("asset_id", "sha256:" + hashlib.sha256(canonical_json(identity)).hexdigest())
        with _CatalogLock(self.path):
            data = self.read()
            existing = next(
                (item for item in data["assets"] if item.get("asset_id") == record["asset_id"]),
                None,
            )
            if existing is not None:
                return existing
            record.setdefault("registered_at", _now())
            data["assets"].append(record)
            data["assets"].sort(key=lambda item: item["asset_id"])
            data["catalog_digest"] = hashlib.sha256(canonical_json(data["assets"])).hexdigest()
            _atomic_json(self.path, data)
        return record

    def cached_request(self, request_key: str, object_store: str | Path) -> dict[str, Any] | None:
        """Return the newest locally available revision for a canonical request."""
        matches = [
            asset for asset in self.read()["assets"] if asset.get("request_key") == request_key
        ]
        for asset in sorted(matches, key=lambda item: item.get("registered_at", ""), reverse=True):
            try:
                with ObjectStore(object_store).open(asset["content_digest"]) as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
                if digest != asset["content_digest"]:
                    raise WatershedDataError(
                        f"cached object is corrupt for asset {asset.get('asset_id')}"
                    )
                return asset
            except FileNotFoundError:
                continue
        return None

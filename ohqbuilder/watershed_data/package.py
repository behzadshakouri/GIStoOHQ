from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .schemas import PackageManifest, SiteSpec, WatershedDataError, canonical_json


def _package_qc_summary(
    destination: Path, allowed_asset_ids: set[str] | None = None,
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    """Aggregate stable QC reports already materialized in the package tree."""
    reports = sorted((destination / "quality_control").rglob("*.json"))
    if not reports:
        return "not_run", (), {}
    failed_severities: set[str] = set()
    failed_rule_ids: set[str] = set()
    qc_policies: dict[str, str] = {}
    for report in reports:
        try:
            document = json.loads(report.read_text(encoding="utf-8"))
            if document.get("schema_name") != "QCReport":
                raise ValueError("schema_name is not QCReport")
            if document.get("schema_version") != "1.0":
                raise ValueError("schema_version is not 1.0")
            policy_version = document.get("policy_version")
            policy_digest = document.get("policy_digest")
            if policy_version is not None or policy_digest is not None:
                if not isinstance(policy_version, str) or not policy_version:
                    raise TypeError("policy_version is not a non-empty string")
                if not isinstance(policy_digest, str) or len(policy_digest) != 64 or any(
                    char not in "0123456789abcdef" for char in policy_digest
                ):
                    raise TypeError("policy_digest is not a lowercase SHA-256 value")
                previous_digest = qc_policies.setdefault(policy_version, policy_digest)
                if previous_digest != policy_digest:
                    raise ValueError(
                        f"policy_version {policy_version!r} has conflicting digests"
                    )
            results = document["results"]
            if not isinstance(results, list):
                raise TypeError("results is not a list")
            for result in results:
                if not isinstance(result, dict):
                    raise TypeError("result is not an object")
                rule_id = result.get("rule_id")
                if not isinstance(rule_id, str) or "." not in rule_id:
                    raise TypeError("rule_id is not a stable dotted identifier")
                severity = result["severity"]
                if severity not in {"error", "warning", "information"}:
                    raise ValueError(f"invalid severity {severity!r}")
                if not isinstance(result["passed"], bool):
                    raise TypeError("passed is not boolean")
                if not isinstance(result.get("message"), str) or not result["message"]:
                    raise TypeError("message is not a non-empty string")
                asset_ids = result.get("asset_ids")
                if not isinstance(asset_ids, list) or any(
                    not isinstance(asset_id, str) or not asset_id for asset_id in asset_ids
                ):
                    raise TypeError("asset_ids is not an array of non-empty strings")
                if allowed_asset_ids is not None:
                    unknown_asset_ids = sorted(set(asset_ids) - allowed_asset_ids)
                    if unknown_asset_ids:
                        raise ValueError(
                            "asset_ids reference assets outside the package catalog: "
                            + ", ".join(unknown_asset_ids)
                        )
                if not isinstance(result.get("details"), dict):
                    raise TypeError("details is not an object")
                if not result["passed"]:
                    failed_severities.add(severity)
                    failed_rule_ids.add(rule_id)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise WatershedDataError(f"invalid package QC report {report}: {exc}") from exc
    if "error" in failed_severities:
        status = "fail"
    elif "warning" in failed_severities:
        status = "warning"
    else:
        status = "pass"
    return status, tuple(sorted(failed_rule_ids)), dict(sorted(qc_policies.items()))


def freeze_package(
    *, site_spec: str | Path, catalog: str | Path, output: str | Path,
    include_raw: str = "referenced", object_store: str | Path | None = None,
    redistributable: bool = False, producer_version: str = "0.1.0",
) -> Path:
    if include_raw not in {"none", "referenced", "all"}:
        raise WatershedDataError("include_raw must be none, referenced, or all")
    if include_raw == "all" and object_store is None:
        raise WatershedDataError("--object-store is required when --include-raw=all")
    spec = SiteSpec.from_file(site_spec)
    catalog_data = AssetCatalog(catalog).read()
    catalog_digest = catalog_data.get("catalog_digest") or hashlib.sha256(
        canonical_json(catalog_data["assets"])
    ).hexdigest()
    asset_ids = tuple(sorted(asset["asset_id"] for asset in catalog_data["assets"]))
    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    package_qc_status, failed_qc_rule_ids, qc_policy_digests = _package_qc_summary(
        destination, set(asset_ids)
    )
    sidecar_checksums = {
        path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("quality_control", "provenance")
        for path in sorted((destination / directory).rglob("*.json"))
        if path.is_file()
    }
    identity = {
        "site_spec_digest": spec.digest, "catalog_digest": catalog_digest,
        "included_asset_ids": asset_ids, "raw_inclusion": include_raw,
        "sidecar_checksums": sidecar_checksums,
    }
    package_id = "sha256:" + hashlib.sha256(canonical_json(identity)).hexdigest()
    (destination / "site_spec.yaml").write_text(
        yaml.safe_dump(spec.to_dict(), sort_keys=False), encoding="utf-8"
    )
    _atomic_json(destination / "catalog.json", catalog_data)
    if include_raw == "all":
        store = ObjectStore(object_store)
        for asset in catalog_data["assets"]:
            digest = asset["content_digest"]
            target = destination / "raw" / "sha256" / digest[:2] / digest[2:]
            target.parent.mkdir(parents=True, exist_ok=True)
            with store.open(digest) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
    manifest = PackageManifest.from_dict({
        "schema_name": "PackageManifest", "schema_version": "1.1",
        "package_id": package_id, "site_id": spec.site_id, "site_spec_digest": spec.digest,
        "catalog_digest": catalog_digest, "included_asset_ids": asset_ids,
        "producer": "GIStoOHQ", "producer_version": producer_version,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "raw_inclusion": include_raw, "self_contained": include_raw == "all",
        "redistributable": redistributable, "package_qc_status": package_qc_status,
        "failed_qc_rule_ids": failed_qc_rule_ids,
        "qc_policy_digests": qc_policy_digests,
        "sidecar_checksums": sidecar_checksums,
    })
    _atomic_json(destination / "manifest.json", manifest.to_dict())
    return destination / "manifest.json"


def validate_package(path: str | Path) -> PackageManifest:
    root = Path(path).expanduser().resolve()
    try:
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatershedDataError(f"could not read package manifest: {exc}") from exc
    manifest = PackageManifest.from_dict(data)
    spec = SiteSpec.from_file(root / "site_spec.yaml")
    catalog = AssetCatalog(root / "catalog.json").read()
    if spec.digest != manifest.site_spec_digest:
        raise WatershedDataError("package SiteSpec digest does not match manifest")
    if catalog.get("catalog_digest") != manifest.catalog_digest:
        raise WatershedDataError("package catalog digest does not match manifest")
    ids = tuple(sorted(asset["asset_id"] for asset in catalog["assets"]))
    if ids != tuple(manifest.included_asset_ids):
        raise WatershedDataError("package asset IDs do not match manifest")
    actual_qc_status, actual_failed_rules, actual_qc_policies = _package_qc_summary(root, set(ids))
    if (manifest.package_qc_status != actual_qc_status
            or manifest.failed_qc_rule_ids != actual_failed_rules
            or manifest.qc_policy_digests != actual_qc_policies):
        raise WatershedDataError("package QC summary does not match its sidecars")
    identity = {
        "site_spec_digest": manifest.site_spec_digest,
        "catalog_digest": manifest.catalog_digest,
        "included_asset_ids": manifest.included_asset_ids,
        "raw_inclusion": manifest.raw_inclusion,
        "sidecar_checksums": manifest.sidecar_checksums,
    }
    expected_id = "sha256:" + hashlib.sha256(canonical_json(identity)).hexdigest()
    if manifest.package_id != expected_id:
        raise WatershedDataError("package identity does not match manifest contents")
    for relative, expected_digest in manifest.sidecar_checksums.items():
        sidecar = root / relative
        if not sidecar.is_file() or hashlib.sha256(sidecar.read_bytes()).hexdigest() != expected_digest:
            raise WatershedDataError(f"missing or corrupt package sidecar: {relative}")
    if manifest.self_contained:
        for asset in catalog["assets"]:
            digest = asset["content_digest"]
            raw = root / "raw" / "sha256" / digest[:2] / digest[2:]
            if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest() != digest:
                raise WatershedDataError(f"missing or corrupt packaged raw object: {digest}")
    return manifest

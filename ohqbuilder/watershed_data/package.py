from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .schemas import PackageManifest, SiteSpec, WatershedDataError, canonical_json


@dataclass(frozen=True)
class _PackageQCSummary:
    status: str
    failed_rule_ids: tuple[str, ...]
    policy_digests: dict[str, str]


def _qc_summary_matches_manifest(
    manifest: PackageManifest, summary: _PackageQCSummary,
) -> bool:
    """Compare a manifest with the current, named QC summary contract."""
    if manifest.package_qc_status != summary.status:
        return False
    if manifest.schema_version in {"1.1", "1.2"}:
        return (
            manifest.failed_qc_rule_ids == summary.failed_rule_ids
            and manifest.qc_policy_digests == summary.policy_digests
        )
    return True


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_checksums(root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for directory in ("quality_control", "provenance"):
        sidecar_root = root / directory
        if sidecar_root.is_symlink():
            raise WatershedDataError(
                f"package sidecar paths must not be symbolic links: {directory}"
            )
        for path in sorted(sidecar_root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise WatershedDataError(
                    f"package sidecar paths must not be symbolic links: {relative}"
                )
            if path.is_file() and path.suffix == ".json":
                checksums[relative] = _file_sha256(path)
    return checksums


def _package_qc_summary(
    destination: Path, allowed_asset_ids: set[str] | None = None,
) -> _PackageQCSummary:
    """Aggregate stable QC reports already materialized in the package tree."""
    reports = sorted((destination / "quality_control").rglob("*.json"))
    if not reports:
        return _PackageQCSummary("not_run", (), {})
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
    return _PackageQCSummary(
        status,
        tuple(sorted(failed_rule_ids)),
        dict(sorted(qc_policies.items())),
    )


def _validation_policy_summary(assets: list[dict[str, object]]) -> dict[str, str]:
    policies: dict[str, str] = {}
    for asset in assets:
        version = asset.get("validation_policy_version")
        digest = asset.get("validation_policy_digest")
        if version is None and digest is None:
            continue
        if not isinstance(version, str) or not version:
            raise WatershedDataError("asset validation policy version must be non-empty")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise WatershedDataError("asset validation policy digest must be lowercase SHA-256")
        previous = policies.setdefault(version, digest)
        if previous != digest:
            raise WatershedDataError(f"validation policy {version!r} has conflicting digests")
    return dict(sorted(policies.items()))


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
    validation_policy_digests = _validation_policy_summary(catalog_data["assets"])
    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    # A failed refresh must never leave an older manifest claiming the new tree.
    (destination / "manifest.json").unlink(missing_ok=True)
    sidecar_checksums = _sidecar_checksums(destination)
    qc_summary = _package_qc_summary(
        destination, set(asset_ids)
    )
    identity = {
        "site_spec_digest": spec.digest, "catalog_digest": catalog_digest,
        "included_asset_ids": asset_ids, "raw_inclusion": include_raw,
        "sidecar_checksums": sidecar_checksums,
    }
    package_id = "sha256:" + hashlib.sha256(canonical_json(identity)).hexdigest()
    site_spec_target = destination / "site_spec.yaml"
    site_spec_temporary = site_spec_target.with_suffix(".yaml.tmp")
    site_spec_temporary.write_text(
        yaml.safe_dump(spec.to_dict(), sort_keys=False), encoding="utf-8"
    )
    site_spec_temporary.replace(site_spec_target)
    _atomic_json(destination / "catalog.json", catalog_data)
    if include_raw == "all":
        store = ObjectStore(object_store)
        for asset in catalog_data["assets"]:
            digest = asset["content_digest"]
            target = destination / "raw" / "sha256" / digest[:2] / digest[2:]
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            copied_digest = hashlib.sha256()
            with store.open(digest) as source, temporary.open("wb") as sink:
                while chunk := source.read(1024 * 1024):
                    sink.write(chunk)
                    copied_digest.update(chunk)
            if copied_digest.hexdigest() != digest:
                temporary.unlink(missing_ok=True)
                raise WatershedDataError(f"copied raw object failed checksum: {digest}")
            temporary.replace(target)
    manifest = PackageManifest.from_dict({
        "schema_name": "PackageManifest", "schema_version": "1.2",
        "package_id": package_id, "site_id": spec.site_id, "site_spec_digest": spec.digest,
        "catalog_digest": catalog_digest, "included_asset_ids": asset_ids,
        "producer": "GIStoOHQ", "producer_version": producer_version,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "raw_inclusion": include_raw, "self_contained": include_raw == "all",
        "redistributable": redistributable, "package_qc_status": qc_summary.status,
        "failed_qc_rule_ids": qc_summary.failed_rule_ids,
        "qc_policy_digests": qc_summary.policy_digests,
        "validation_policy_digests": validation_policy_digests,
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
    actual_sidecar_checksums = _sidecar_checksums(root)
    if actual_sidecar_checksums.keys() != manifest.sidecar_checksums.keys():
        raise WatershedDataError("package sidecar inventory does not match manifest")
    for relative, expected_digest in manifest.sidecar_checksums.items():
        if actual_sidecar_checksums[relative] != expected_digest:
            raise WatershedDataError(f"missing or corrupt package sidecar: {relative}")
    qc_summary = _package_qc_summary(root, set(ids))
    actual_validation_policies = _validation_policy_summary(catalog["assets"])
    if not _qc_summary_matches_manifest(manifest, qc_summary):
        raise WatershedDataError("package QC summary does not match its sidecars")
    if (manifest.schema_version == "1.2"
            and manifest.validation_policy_digests != actual_validation_policies):
        raise WatershedDataError("package validation policy summary does not match its catalog")
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
    if manifest.self_contained:
        for asset in catalog["assets"]:
            digest = asset["content_digest"]
            raw = root / "raw" / "sha256" / digest[:2] / digest[2:]
            if not raw.is_file() or _file_sha256(raw) != digest:
                raise WatershedDataError(f"missing or corrupt packaged raw object: {digest}")
    if manifest.schema_version in {"1.0", "1.1"}:
        manifest = replace(
            manifest,
            failed_qc_rule_ids=qc_summary.failed_rule_ids,
            qc_policy_digests=qc_summary.policy_digests,
            validation_policy_digests=actual_validation_policies,
        )
    return manifest

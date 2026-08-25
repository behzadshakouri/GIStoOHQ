from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml


class WatershedDataError(ValueError):
    """Raised when a watershed-data document violates its contract."""


def _utc_timestamp(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise WatershedDataError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WatershedDataError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WatershedDataError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    """Serialize JSON data deterministically for identity calculations."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WatershedDataError(f"value is not canonical JSON data: {exc}") from exc


def canonical_request_key(
    provider: str,
    endpoint: str,
    parameters: dict[str, Any],
    product_version: str,
    *,
    method: str = "GET",
) -> str:
    """Return the logical request identity, independent of response bytes."""

    document = {
        "endpoint": endpoint,
        "method": method.upper(),
        "parameters": parameters,
        "product_version": product_version,
        "provider": provider,
    }
    return hashlib.sha256(canonical_json(document)).hexdigest()


@dataclass(frozen=True)
class SiteSpec:
    site_id: str
    name: str
    longitude: float
    latitude: float
    study_start: str
    study_end: str
    target_timestep: str
    sources: dict[str, Any]
    schema_version: str = "1.1"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteSpec":
        if not isinstance(data, dict):
            raise WatershedDataError("SiteSpec must be an object")
        geometry = data.get("geometry") or {}
        outlet = geometry.get("outlet") or {}
        period = data.get("study_period") or {}
        site_id = str(data.get("site_id") or "").strip()
        if not site_id:
            raise WatershedDataError("site_id is required")
        try:
            longitude = float(outlet["longitude"])
            latitude = float(outlet["latitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WatershedDataError("geometry.outlet longitude and latitude are required") from exc
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise WatershedDataError("outlet longitude or latitude is outside its valid range")
        start = _utc_timestamp(period.get("start"), "study_period.start")
        end = _utc_timestamp(period.get("end"), "study_period.end")
        if start >= end:
            raise WatershedDataError("study_period.start must precede study_period.end")
        sources = data.get("sources") or {}
        if not isinstance(sources, dict):
            raise WatershedDataError("sources must be an object")
        return cls(
            site_id=site_id,
            name=str(data.get("name") or site_id),
            longitude=longitude,
            latitude=latitude,
            study_start=start,
            study_end=end,
            target_timestep=str(data.get("target_timestep") or "1h"),
            sources=sources,
            schema_version=str(data.get("schema_version") or "1.0"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "SiteSpec":
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise WatershedDataError(f"could not read SiteSpec {path}: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": "SiteSpec",
            "schema_version": self.schema_version,
            "site_id": self.site_id,
            "name": self.name,
            "geometry": {
                "outlet": {"longitude": self.longitude, "latitude": self.latitude},
                "supplied_basin": None,
            },
            "study_period": {"start": self.study_start, "end": self.study_end},
            "target_timestep": self.target_timestep,
            "sources": self.sources,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()


QCSeverity = Literal["error", "warning", "information"]


@dataclass(frozen=True)
class QCResult:
    """One stable, machine-readable generic quality-control result."""

    rule_id: str
    severity: QCSeverity
    passed: bool
    message: str
    asset_ids: tuple[str, ...] = ()
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.rule_id or "." not in self.rule_id:
            raise WatershedDataError("QC rule_id must be a stable dotted identifier")
        if self.severity not in {"error", "warning", "information"}:
            raise WatershedDataError(f"invalid QC severity: {self.severity}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "passed": self.passed,
            "message": self.message,
            "asset_ids": list(self.asset_ids),
            "details": self.details or {},
        }


@dataclass(frozen=True)
class ProvenanceActivity:
    """Lineage for a derivation that creates new catalog assets."""

    activity_id: str
    transformation_name: str
    transformation_version: str
    parent_asset_ids: tuple[str, ...]
    output_asset_ids: tuple[str, ...]
    parameters: dict[str, Any]
    software_version: str
    started_at: str
    completed_at: str

    def __post_init__(self) -> None:
        start = _utc_timestamp(self.started_at, "started_at")
        end = _utc_timestamp(self.completed_at, "completed_at")
        if start > end:
            raise WatershedDataError("provenance started_at must not follow completed_at")
        if not self.parent_asset_ids or not self.output_asset_ids:
            raise WatershedDataError("provenance requires parent and output asset IDs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "transformation_name": self.transformation_name,
            "transformation_version": self.transformation_version,
            "parent_asset_ids": list(self.parent_asset_ids),
            "output_asset_ids": list(self.output_asset_ids),
            "parameters": self.parameters,
            "software_version": self.software_version,
            "started_at": _utc_timestamp(self.started_at, "started_at"),
            "completed_at": _utc_timestamp(self.completed_at, "completed_at"),
        }


@dataclass(frozen=True)
class PackageManifest:
    package_id: str
    site_id: str
    site_spec_digest: str
    catalog_digest: str
    included_asset_ids: tuple[str, ...]
    producer: str
    producer_version: str
    generated_at: str
    raw_inclusion: Literal["none", "referenced", "all"]
    self_contained: bool
    redistributable: bool
    package_qc_status: Literal["pass", "warning", "fail", "not_run"] = "not_run"
    failed_qc_rule_ids: tuple[str, ...] = ()
    qc_policy_digests: dict[str, str] = field(default_factory=dict)
    sidecar_checksums: dict[str, str] = field(default_factory=dict)
    schema_version: str = "1.1"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackageManifest":
        if data.get("schema_name") != "PackageManifest" or data.get("schema_version") not in {
            "1.0", "1.1",
        }:
            raise WatershedDataError("PackageManifest schema must be version 1.0 or 1.1")
        try:
            manifest = cls(
                package_id=str(data["package_id"]), site_id=str(data["site_id"]),
                site_spec_digest=str(data["site_spec_digest"]),
                catalog_digest=str(data["catalog_digest"]),
                included_asset_ids=tuple(data["included_asset_ids"]),
                producer=str(data["producer"]), producer_version=str(data["producer_version"]),
                generated_at=_utc_timestamp(data["generated_at"], "generated_at"),
                raw_inclusion=data["raw_inclusion"], self_contained=bool(data["self_contained"]),
                redistributable=bool(data["redistributable"]),
                package_qc_status=data.get("package_qc_status", "not_run"),
                failed_qc_rule_ids=tuple(data.get("failed_qc_rule_ids", ())),
                qc_policy_digests=dict(data.get("qc_policy_digests", {})),
                sidecar_checksums=dict(data.get("sidecar_checksums", {})),
                schema_version=str(data["schema_version"]),
            )
        except (KeyError, TypeError) as exc:
            raise WatershedDataError(f"invalid PackageManifest: missing {exc}") from exc
        if manifest.raw_inclusion not in {"none", "referenced", "all"}:
            raise WatershedDataError("raw_inclusion must be none, referenced, or all")
        if manifest.self_contained != (manifest.raw_inclusion == "all"):
            raise WatershedDataError("self_contained must be true exactly when raw_inclusion is all")
        if manifest.package_qc_status not in {"pass", "warning", "fail", "not_run"}:
            raise WatershedDataError("package_qc_status must be pass, warning, fail, or not_run")
        if any(not isinstance(rule_id, str) or not rule_id for rule_id in manifest.failed_qc_rule_ids):
            raise WatershedDataError("failed_qc_rule_ids must contain non-empty strings")
        if tuple(sorted(set(manifest.failed_qc_rule_ids))) != manifest.failed_qc_rule_ids:
            raise WatershedDataError("failed_qc_rule_ids must be sorted and unique")
        for policy_version, policy_digest in manifest.qc_policy_digests.items():
            if not policy_version:
                raise WatershedDataError("qc_policy_digests keys must be non-empty")
            if len(policy_digest) != 64 or any(
                char not in "0123456789abcdef" for char in policy_digest
            ):
                raise WatershedDataError("qc_policy_digests values must be lowercase SHA-256")
        for path, digest in manifest.sidecar_checksums.items():
            if not path or Path(path).is_absolute() or ".." in Path(path).parts:
                raise WatershedDataError("sidecar checksum paths must be safe relative paths")
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise WatershedDataError("sidecar checksums must be lowercase SHA-256 values")
        return manifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_name": "PackageManifest", "schema_version": self.schema_version,
            "package_id": self.package_id, "site_id": self.site_id,
            "site_spec_digest": self.site_spec_digest, "catalog_digest": self.catalog_digest,
            "included_asset_ids": list(self.included_asset_ids), "producer": self.producer,
            "producer_version": self.producer_version, "generated_at": self.generated_at,
            "raw_inclusion": self.raw_inclusion, "self_contained": self.self_contained,
            "redistributable": self.redistributable,
            "package_qc_status": self.package_qc_status,
            "failed_qc_rule_ids": list(self.failed_qc_rule_ids),
            "qc_policy_digests": dict(sorted(self.qc_policy_digests.items())),
            "sidecar_checksums": dict(sorted(self.sidecar_checksums.items())),
        }

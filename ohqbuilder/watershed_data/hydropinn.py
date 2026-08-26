from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from .catalog import AssetCatalog, ObjectStore, _atomic_json
from .package import validate_package
from .schemas import HydroPINNExportManifest, SiteSpec, WatershedDataError


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_export(
    destination: Path, *, site_id: str,
) -> tuple[HydroPINNExportManifest, Path] | None:
    if not destination.exists():
        return None
    manifest_path = destination / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = HydroPINNExportManifest.from_dict(document)
    except (OSError, json.JSONDecodeError, WatershedDataError) as exc:
        raise WatershedDataError(
            f"HydroPINN export destination exists but is not a valid export: {destination}"
        ) from exc
    if manifest.site_id != site_id:
        raise WatershedDataError(
            "HydroPINN export destination belongs to a different site: "
            f"{manifest.site_id}"
        )
    return manifest, manifest_path


def _export_files_are_valid(
    destination: Path, manifest: HydroPINNExportManifest,
) -> bool:
    if not (destination / "variables.json").is_file():
        return False
    return all(
        (path := destination / asset["path"]).is_file()
        and _file_sha256(path) == asset["sha256"]
        for asset in manifest.assets
    )


def _publish_export_directory(staged: Path, final: Path) -> None:
    """Replace a same-site export without exposing a partial new directory."""
    backup: Path | None = None
    if final.exists():
        backup = Path(tempfile.mkdtemp(prefix=f".{final.name}.backup-", dir=final.parent))
        backup.rmdir()
        final.replace(backup)
    try:
        staged.replace(final)
    except BaseException:
        if backup is not None and backup.exists() and not final.exists():
            backup.replace(final)
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup)


def export_hydropinn(
    *, package: str | Path, object_store: str | Path | None,
    output: str | Path, profile: str = "water-balance-v1",
    require_qc_pass: bool = False,
) -> Path:
    """Export selected generic temporal assets without ML preprocessing."""
    if profile != "water-balance-v1":
        raise WatershedDataError(f"unsupported HydroPINN profile: {profile}")
    root = Path(package).expanduser().resolve()
    package_manifest = validate_package(root)
    spec = SiteSpec.from_file(root / "site_spec.yaml")
    if package_manifest.package_qc_status == "fail":
        raise WatershedDataError(
            "HydroPINN export refused because the source package has failed QC: "
            + ", ".join(package_manifest.failed_qc_rule_ids)
        )
    if require_qc_pass and package_manifest.package_qc_status != "pass":
        raise WatershedDataError(
            "HydroPINN export requires passing package QC; source status is "
            + package_manifest.package_qc_status
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
    final_destination = Path(output).expanduser().resolve()
    existing = _existing_export(final_destination, site_id=package_manifest.site_id)
    expected_gate = "require_pass" if require_qc_pass else "reject_fail"
    if existing is not None:
        existing_manifest, existing_manifest_path = existing
        if (
            existing_manifest.source_package_id == package_manifest.package_id
            and existing_manifest.profile == profile
            and existing_manifest.qc_gate == expected_gate
            and _export_files_are_valid(final_destination, existing_manifest)
        ):
            return existing_manifest_path
    final_destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{final_destination.name}.staging-", dir=final_destination.parent
    ) as temporary:
        destination = Path(temporary)
        observations = destination / "observations"
        observations.mkdir()
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
                "sha256": _file_sha256(target),
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
        manifest = HydroPINNExportManifest.from_dict({
            "schema_name": "HydroPINNExport", "schema_version": "1.2",
            "profile": profile, "source_package_id": package_manifest.package_id,
            "site_id": package_manifest.site_id,
            "study_start": spec.study_start, "study_end": spec.study_end,
            "source_package_qc_status": package_manifest.package_qc_status,
            "source_failed_qc_rule_ids": list(package_manifest.failed_qc_rule_ids),
            "source_qc_policy_digests": dict(package_manifest.qc_policy_digests),
            "source_validation_policy_digests": dict(
                package_manifest.validation_policy_digests
            ),
            "qc_gate": expected_gate,
            "assets": exported,
            "transformations_not_performed": [
                "normalization", "imputation", "lag_construction", "feature_selection",
                "train_validation_test_partitioning",
            ],
        })
        _atomic_json(destination / "manifest.json", manifest.to_dict())
        _publish_export_directory(destination, final_destination)
    return final_destination / "manifest.json"

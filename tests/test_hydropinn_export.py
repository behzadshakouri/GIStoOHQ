import io
import json
from pathlib import Path

import pytest

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.hydropinn import export_hydropinn
from ohqbuilder.watershed_data.package import freeze_package, validate_package
from ohqbuilder.watershed_data.schemas import HydroPINNExportManifest, WatershedDataError
from ohqbuilder.watershed_data.temporal import harmonize_asset
from ohqbuilder.watershed_data.workflow import write_site_spec


def test_hydropinn_export_is_thin_deterministic_and_named(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    store = ObjectStore(tmp_path / "store")
    raw = Path("tests/fixtures/nasa_power_hourly.json").read_bytes()
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "nasa-power", "product": "historical-meteorology",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json",
    })
    package = tmp_path / "package"
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=package / "quality_control" / "temporal.json",
        provenance_output=package / "provenance" / "temporal.json",
    )
    freeze_package(site_spec=site, catalog=catalog.path, output=package)
    manifest_path = export_hydropinn(
        package=package, object_store=store.root, output=tmp_path / "hydropinn",
        require_qc_pass=True,
    )
    manifest = json.loads(manifest_path.read_text())
    variables = json.loads((tmp_path / "hydropinn" / "variables.json").read_text())
    assert manifest["profile"] == "water-balance-v1"
    assert manifest["schema_version"] == "1.1"
    assert manifest["source_package_qc_status"] == "pass"
    assert manifest["source_failed_qc_rule_ids"] == []
    assert manifest["source_qc_policy_digests"].keys() == {"temporal-qc-v3"}
    assert manifest["source_validation_policy_digests"] == {}
    assert manifest["qc_gate"] == "require_pass"
    assert "normalization" in manifest["transformations_not_performed"]
    assert {item["name"] for item in variables["variables"]} == {"PRECTOTCORR", "T2M"}
    assert variables["variables"][0]["normalization"] is None
    assert (tmp_path / "hydropinn" / "observations" / "temporal_1.csv").is_file()
    with pytest.raises(WatershedDataError, match="destination already exists"):
        export_hydropinn(
            package=package, object_store=store.root, output=tmp_path / "hydropinn",
            require_qc_pass=True,
        )


def test_hydropinn_export_refuses_failed_package_qc(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    catalog = AssetCatalog(tmp_path / "catalog.json")
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(b"{}"))
    catalog.register({
        "provider": "test", "product": "invalid-temporal",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "processing_status": "native",
    })
    package = tmp_path / "package"
    qc_dir = package / "quality_control"
    qc_dir.mkdir(parents=True)
    (qc_dir / "failed.json").write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0",
        "results": [{
            "rule_id": "temporal.finite_values", "severity": "error", "passed": False,
            "message": "1 non-finite numeric observation", "asset_ids": [], "details": {},
        }],
    }))
    manifest_path = freeze_package(site_spec=site, catalog=catalog.path, output=package)
    assert json.loads(manifest_path.read_text())["failed_qc_rule_ids"] == [
        "temporal.finite_values"
    ]
    original_manifest = manifest_path.read_text()
    edited_manifest = json.loads(original_manifest)
    edited_manifest["schema_version"] = "9.0"
    manifest_path.write_text(json.dumps(edited_manifest))
    with pytest.raises(WatershedDataError, match="schema must be version"):
        validate_package(package)
    manifest_path.write_text(original_manifest)
    edited_manifest = json.loads(original_manifest)
    edited_manifest["failed_qc_rule_ids"] = []
    manifest_path.write_text(json.dumps(edited_manifest))
    with pytest.raises(WatershedDataError, match="QC summary does not match"):
        validate_package(package)
    manifest_path.write_text(original_manifest)
    with pytest.raises(
        WatershedDataError, match=r"source package has failed QC: temporal\.finite_values"
    ):
        export_hydropinn(package=package, object_store=store.root, output=tmp_path / "hydropinn")
    assert not (tmp_path / "hydropinn").exists()

    qc_report = json.loads((qc_dir / "failed.json").read_text())
    qc_report["results"][0]["severity"] = "warning"
    (qc_dir / "failed.json").write_text(json.dumps(qc_report))
    freeze_package(site_spec=site, catalog=catalog.path, output=package)
    with pytest.raises(WatershedDataError, match="requires passing package QC"):
        export_hydropinn(
            package=package,
            object_store=store.root,
            output=tmp_path / "strict-hydropinn",
            require_qc_pass=True,
        )
    assert not (tmp_path / "strict-hydropinn").exists()


def test_hydropinn_manifest_rejects_unsafe_asset_paths():
    with pytest.raises(WatershedDataError, match="safe and relative"):
        HydroPINNExportManifest.from_dict({
            "schema_name": "HydroPINNExport", "schema_version": "1.1",
            "profile": "water-balance-v1", "source_package_id": "sha256:" + "a" * 64,
            "site_id": "test", "source_package_qc_status": "pass",
            "source_failed_qc_rule_ids": [], "source_qc_policy_digests": {},
            "source_validation_policy_digests": {},
            "qc_gate": "require_pass",
            "assets": [{
                "asset_id": "sha256:" + "c" * 64,
                "path": "../outside.csv", "sha256": "b" * 64,
            }],
            "transformations_not_performed": [],
        })


def test_hydropinn_manifest_rejects_invalid_source_package_id():
    with pytest.raises(WatershedDataError, match="source package ID"):
        HydroPINNExportManifest.from_dict({
            "schema_name": "HydroPINNExport", "schema_version": "1.1",
            "profile": "water-balance-v1", "source_package_id": "not-a-digest",
            "site_id": "test", "source_package_qc_status": "pass",
            "source_failed_qc_rule_ids": [], "source_qc_policy_digests": {},
            "source_validation_policy_digests": {}, "qc_gate": "require_pass",
            "assets": [], "transformations_not_performed": [],
        })

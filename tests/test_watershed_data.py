import ast
import inspect
import io
import json
import threading

import pytest

from ohqbuilder.cli import main
from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.schemas import (
    SiteSpec,
    WatershedDataError,
    canonical_request_key,
)
from ohqbuilder.watershed_data.workflow import acquire_url
from ohqbuilder.watershed_data.package import freeze_package, validate_package
from ohqbuilder.watershed_data import package as package_module
from ohqbuilder.watershed_data.schemas import ProvenanceActivity, QCResult


def test_request_identity_is_canonical_and_separate_from_content():
    first = canonical_request_key("usgs", "https://example.test", {"b": 2, "a": 1}, "1")
    second = canonical_request_key("usgs", "https://example.test", {"a": 1, "b": 2}, "1")
    changed = canonical_request_key("usgs", "https://example.test", {"a": 2, "b": 2}, "1")

    assert first == second
    assert first != changed


def test_site_spec_requires_timezone_and_valid_period():
    with pytest.raises(WatershedDataError, match="timezone"):
        SiteSpec.from_dict(
            {
                "site_id": "test",
                "geometry": {"outlet": {"longitude": -77, "latitude": 39}},
                "study_period": {"start": "2020-01-01", "end": "2021-01-01T00:00:00Z"},
            }
        )


def test_site_spec_default_schema_version_remains_1_0():
    spec = SiteSpec(
        site_id="test",
        name="Test",
        longitude=-77.0,
        latitude=39.0,
        study_start="2020-01-01T00:00:00Z",
        study_end="2021-01-01T00:00:00Z",
        target_timestep="1h",
        sources={},
    )

    assert spec.schema_version == "1.0"


def test_object_store_deduplicates_and_catalog_registers_once(tmp_path):
    store = ObjectStore(tmp_path / "cache")
    first = store.put(io.BytesIO(b"weather data"))
    second = store.put(io.BytesIO(b"weather data"))
    assert first.content_digest == second.content_digest
    assert first.path == second.path
    assert first.path.read_bytes() == b"weather data"

    catalog = AssetCatalog(tmp_path / "catalog.json")
    asset = {
        "provider": "example",
        "product": "weather",
        "content_digest": first.content_digest,
        "size": first.size,
        "media_type": "text/csv",
    }
    catalog.register(asset)
    catalog.register(asset)
    assert len(json.loads((tmp_path / "catalog.json").read_text())["assets"]) == 1


def test_object_store_refuses_to_replace_corrupt_immutable_object(tmp_path):
    store = ObjectStore(tmp_path / "cache")
    stored = store.put(io.BytesIO(b"weather data"))
    stored.path.write_bytes(b"corrupt")
    with pytest.raises(WatershedDataError, match="will not be overwritten"):
        store.put(io.BytesIO(b"weather data"))
    assert stored.path.read_bytes() == b"corrupt"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("content_digest", "../bad", "lowercase SHA-256"),
        ("size", -1, "non-negative integer"),
        ("provider", "", "non-empty string"),
        ("product", "", "non-empty string"),
        ("media_type", "json", "type/subtype"),
    ],
)
def test_catalog_rejects_invalid_asset_metadata(tmp_path, field, value, message):
    asset = {
        "provider": "example", "product": "weather", "content_digest": "0" * 64,
        "size": 0, "media_type": "application/json",
    }
    asset[field] = value
    with pytest.raises(WatershedDataError, match=message):
        AssetCatalog(tmp_path / "catalog.json").register(asset)


def test_catalog_lock_prevents_lost_concurrent_registrations(tmp_path):
    catalog = AssetCatalog(tmp_path / "catalog.json")

    def register(index):
        catalog.register({
            "provider": "example", "product": f"weather-{index}",
            "content_digest": f"{index:064x}", "size": index, "media_type": "text/csv",
        })

    threads = [threading.Thread(target=register, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(catalog.read()["assets"]) == 8


def test_catalog_reclaims_lock_left_by_dead_process(tmp_path):
    catalog = AssetCatalog(tmp_path / "catalog.json")
    lock = catalog.path.with_suffix(".json.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("2147483647", encoding="ascii")
    asset = catalog.register({
        "provider": "example", "product": "weather", "content_digest": "a" * 64,
        "size": 0, "media_type": "application/json",
    })
    assert asset["asset_id"].startswith("sha256:")
    assert not lock.exists()


def test_catalog_requires_complete_lineage_for_derived_assets(tmp_path):
    asset = {
        "provider": "example", "product": "derived", "content_digest": "b" * 64,
        "size": 1, "media_type": "text/csv", "processing_status": "derived",
        "parent_asset_ids": ["sha256:parent"],
    }
    with pytest.raises(WatershedDataError, match="transformation_name"):
        AssetCatalog(tmp_path / "catalog.json").register(asset)
    asset.update({
        "transformation_name": "resample", "transformation_version": "1.0",
        "transformation_parameters": {"timestep": "1h"},
    })
    assert AssetCatalog(tmp_path / "catalog.json").register(asset)["processing_status"] == "derived"


def test_catalog_read_rejects_metadata_tampering(tmp_path):
    catalog = AssetCatalog(tmp_path / "catalog.json")
    catalog.register({
        "provider": "example", "product": "weather", "content_digest": "c" * 64,
        "size": 1, "media_type": "application/json",
    })
    document = json.loads(catalog.path.read_text())
    document["assets"][0]["product"] = "tampered"
    catalog.path.write_text(json.dumps(document))
    with pytest.raises(WatershedDataError, match="digest does not match"):
        catalog.read()


def test_acquire_url_rejects_insecure_or_local_paths_before_network(tmp_path):
    with pytest.raises(WatershedDataError, match="HTTPS"):
        acquire_url(
            url="file:///etc/passwd", provider="local", product="unsafe",
            product_version="1", cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
        )


def test_data_cli_creates_and_validates_site_without_affecting_full_run(tmp_path, capsys):
    path = tmp_path / "site.yaml"
    status = main(
        [
            "data", "init-site", "--site-spec", str(path), "--site-id", "hickey_run",
            "--lon", "-76.98", "--lat", "38.92", "--start", "2018-01-01T00:00:00Z",
            "--end", "2025-12-31T23:00:00Z",
        ]
    )
    assert status == 0
    assert main(["data", "validate-site", "--site-spec", str(path)]) == 0
    assert "SiteSpec valid: hickey_run" in capsys.readouterr().out


def test_existing_full_run_parser_does_not_require_data_options():
    from ohqbuilder.cli import build_parser

    args = build_parser().parse_args(
        ["full-run", "--root", "/tmp/root", "--site", "site", "--lon", "-77", "--lat", "39"]
    )
    assert args.command == "full-run"
    assert not hasattr(args, "site_spec")


def test_qc_and_provenance_contracts_reject_invalid_values():
    result = QCResult("temporal.duplicate_timestamps", "warning", False, "duplicates")
    assert result.to_dict()["severity"] == "warning"
    with pytest.raises(WatershedDataError, match="severity"):
        QCResult("temporal.range", "critical", False, "bad")
    with pytest.raises(WatershedDataError, match="parent and output"):
        ProvenanceActivity(
            "activity:1", "resample", "1", (), (), {}, "gistoohq",
            "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
        )


def test_package_manifest_rejects_non_string_policy_digest():
    from ohqbuilder.watershed_data.schemas import PackageManifest

    with pytest.raises(WatershedDataError, match="validation_policy_digests"):
        PackageManifest.from_dict({
            "schema_name": "PackageManifest", "schema_version": "1.2",
            "package_id": "sha256:" + "a" * 64, "site_id": "test",
            "site_spec_digest": "b" * 64, "catalog_digest": "c" * 64,
            "included_asset_ids": [], "producer": "test", "producer_version": "1",
            "generated_at": "2025-01-01T00:00:00Z", "raw_inclusion": "none",
            "self_contained": False, "redistributable": False,
            "validation_policy_digests": {"forecast-validation-v1": 1},
        })


def test_package_manifest_rejects_unsorted_asset_ids():
    from ohqbuilder.watershed_data.schemas import PackageManifest

    with pytest.raises(WatershedDataError, match="sorted and unique"):
        PackageManifest.from_dict({
            "schema_name": "PackageManifest", "schema_version": "1.2",
            "package_id": "sha256:" + "a" * 64, "site_id": "test",
            "site_spec_digest": "b" * 64, "catalog_digest": "c" * 64,
            "included_asset_ids": ["sha256:" + "f" * 64, "sha256:" + "d" * 64],
            "producer": "test", "producer_version": "1",
            "generated_at": "2025-01-01T00:00:00Z", "raw_inclusion": "none",
            "self_contained": False, "redistributable": False,
        })


def test_validate_package_has_one_named_qc_summary_path():
    """Prevent stale positional QC validation from returning during merges."""
    tree = ast.parse(inspect.getsource(package_module.validate_package))
    summary_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_package_qc_summary"
    ]
    positional_assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, (ast.Tuple, ast.List)) for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_package_qc_summary"
    ]

    assert len(summary_calls) == 1
    assert positional_assignments == []


def test_package_manifest_aggregates_qc_results(tmp_path):
    site = tmp_path / "site.yaml"
    site.write_text(
        "site_id: qc\ngeometry:\n  outlet:\n    longitude: -77\n    latitude: 39\n"
        "study_period:\n  start: '2025-01-01T00:00:00Z'\n"
        "  end: '2025-01-02T00:00:00Z'\n"
    )
    catalog = AssetCatalog(tmp_path / "catalog.json")
    asset = catalog.register({
        "provider": "example", "product": "data", "content_digest": "0" * 64,
        "size": 0, "media_type": "application/json",
        "validation_policy_version": "forecast-validation-v1",
        "validation_policy_digest": "c" * 64,
    })
    output = tmp_path / "package"
    qc = output / "quality_control" / "providers" / "temporal.json"
    qc.parent.mkdir(parents=True)
    qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0", "results": [
            {"rule_id": "temporal.missing_values", "severity": "warning", "passed": False,
             "message": "1 missing value", "asset_ids": [asset["asset_id"]], "details": {}}
        ],
    }))
    manifest_path = freeze_package(site_spec=site, catalog=catalog.path, output=output)
    assert json.loads(manifest_path.read_text())["schema_version"] == "1.2"
    assert validate_package(output).validation_policy_digests == {
        "forecast-validation-v1": "c" * 64,
    }
    assert validate_package(output).package_qc_status == "warning"
    legacy_manifest = json.loads(manifest_path.read_text())
    legacy_manifest["schema_version"] = "1.0"
    legacy_manifest.pop("failed_qc_rule_ids")
    legacy_manifest.pop("qc_policy_digests")
    legacy_manifest.pop("validation_policy_digests")
    manifest_path.write_text(json.dumps(legacy_manifest))
    validated_legacy = validate_package(output)
    assert validated_legacy.failed_qc_rule_ids == ("temporal.missing_values",)
    assert validated_legacy.qc_policy_digests == {}
    qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0", "results": [
            {"severity": "information", "passed": True, "message": "invalid",
             "asset_ids": [], "details": {}}
        ],
    }))
    with pytest.raises(WatershedDataError, match="stable dotted identifier"):
        freeze_package(site_spec=site, catalog=catalog.path, output=output)
    assert not manifest_path.exists()
    qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0", "results": [{
            "rule_id": "temporal.missing_values", "severity": "warning", "passed": False,
            "message": "unknown asset", "asset_ids": ["sha256:unknown"], "details": {},
        }],
    }))
    with pytest.raises(WatershedDataError, match="outside the package catalog"):
        freeze_package(site_spec=site, catalog=catalog.path, output=output)
    qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0",
        "policy_version": "temporal-qc-v1", "results": [],
    }))
    with pytest.raises(WatershedDataError, match="policy_digest"):
        freeze_package(site_spec=site, catalog=catalog.path, output=output)
    qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0",
        "policy_version": "temporal-qc-v1", "policy_digest": "a" * 64, "results": [],
    }))
    conflicting_qc = qc.with_name("conflicting.json")
    conflicting_qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0",
        "policy_version": "temporal-qc-v1", "policy_digest": "b" * 64, "results": [],
    }))
    with pytest.raises(WatershedDataError, match="conflicting digests"):
        freeze_package(site_spec=site, catalog=catalog.path, output=output)


def test_freeze_and_validate_self_contained_package(tmp_path):
    site = tmp_path / "site.yaml"
    assert main([
        "data", "init-site", "--site-spec", str(site), "--site-id", "test",
        "--lon", "-77", "--lat", "39", "--start", "2020-01-01T00:00:00Z",
        "--end", "2021-01-01T00:00:00Z",
    ]) == 0
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(b"native observations"))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    catalog.register({
        "provider": "example", "product": "weather", "content_digest": stored.content_digest,
        "size": stored.size, "media_type": "text/csv",
    })
    manifest_path = freeze_package(
        site_spec=site, catalog=catalog.path, output=tmp_path / "package",
        include_raw="all", object_store=store.root,
    )
    assert manifest_path.is_file()
    manifest = validate_package(tmp_path / "package")
    assert manifest.self_contained is True
    assert manifest.raw_inclusion == "all"

    document = json.loads(manifest_path.read_text())
    original_id = document["package_id"]
    document["package_id"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(document))
    with pytest.raises(WatershedDataError, match="identity"):
        validate_package(tmp_path / "package")
    document["package_id"] = original_id
    manifest_path.write_text(json.dumps(document))

    unexpected_sidecar = tmp_path / "package" / "provenance" / "unexpected.json"
    unexpected_sidecar.parent.mkdir()
    unexpected_sidecar.write_text("{}")
    with pytest.raises(WatershedDataError, match="sidecar inventory"):
        validate_package(tmp_path / "package")
    unexpected_sidecar.unlink()

    external_sidecar = tmp_path / "external.json"
    external_sidecar.write_text("{}")
    linked_sidecar = tmp_path / "package" / "provenance" / "linked.json"
    linked_sidecar.symlink_to(external_sidecar)
    with pytest.raises(WatershedDataError, match="must not be symbolic links"):
        validate_package(tmp_path / "package")
    linked_sidecar.unlink()

    external_sidecar.write_text("not JSON")
    linked_qc = tmp_path / "package" / "quality_control" / "linked.json"
    linked_qc.parent.mkdir()
    linked_qc.symlink_to(external_sidecar)
    with pytest.raises(WatershedDataError, match="must not be symbolic links"):
        validate_package(tmp_path / "package")
    linked_qc.unlink()

    raw = next((tmp_path / "package" / "raw").rglob("*"))
    while raw.is_dir():
        raw = next(raw.iterdir())
    raw.write_bytes(b"corrupt")
    with pytest.raises(WatershedDataError, match="corrupt"):
        validate_package(tmp_path / "package")

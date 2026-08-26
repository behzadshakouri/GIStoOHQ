import io
import json

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.doctor import run_data_doctor
from ohqbuilder.watershed_data.package import freeze_package
from ohqbuilder.watershed_data.workflow import write_site_spec


def test_data_doctor_validates_site_catalog_and_object_integrity(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(b"native"))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    catalog.register({
        "provider": "example", "product": "weather", "content_digest": stored.content_digest,
        "size": stored.size, "media_type": "application/octet-stream",
    })
    report = run_data_doctor(site_spec=site, catalog=catalog.path, object_store=store.root)
    assert report["passed"] is True
    assert [check["name"] for check in report["checks"]] == [
        "site_spec", "catalog", "object_store"
    ]

    stored.path.write_bytes(b"corrupt")
    report = run_data_doctor(site_spec=site, catalog=catalog.path, object_store=store.root)
    assert report["passed"] is False
    assert report["checks"][-1]["passed"] is False


def test_data_doctor_reports_missing_catalog_without_crashing(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )

    report = run_data_doctor(site_spec=site, catalog=tmp_path / "missing-catalog.json")

    assert report["passed"] is False
    assert report["checks"][-1]["name"] == "catalog"
    assert report["checks"][-1]["passed"] is False
    assert "does not exist" in report["checks"][-1]["message"]


def test_data_doctor_fails_a_package_with_error_level_qc(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    catalog = AssetCatalog(tmp_path / "catalog.json")
    catalog.register({
        "provider": "example", "product": "weather", "content_digest": "d" * 64,
        "size": 1, "media_type": "application/json",
    })
    package = tmp_path / "package"
    qc = package / "quality_control" / "temporal.json"
    qc.parent.mkdir(parents=True)
    qc.write_text(json.dumps({
        "schema_name": "QCReport", "schema_version": "1.0", "results": [{
            "rule_id": "temporal.physical_range", "severity": "error", "passed": False,
            "message": "1 value outside physical range", "asset_ids": [], "details": {},
        }],
    }))
    freeze_package(site_spec=site, catalog=catalog.path, output=package)
    report = run_data_doctor(site_spec=site, package=package)
    assert report["passed"] is False
    assert report["checks"][-1] == {
        "name": "package_qc", "passed": False,
        "message": "package QC status is fail (temporal.physical_range)",
    }


def test_data_doctor_rejects_package_from_another_catalog(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    packaged_catalog = AssetCatalog(tmp_path / "packaged.json")
    packaged_catalog.register({
        "provider": "example", "product": "one", "content_digest": "a" * 64,
        "size": 1, "media_type": "application/json",
    })
    package = tmp_path / "package"
    freeze_package(site_spec=site, catalog=packaged_catalog.path, output=package)
    other_catalog = AssetCatalog(tmp_path / "other.json")
    other_catalog.register({
        "provider": "example", "product": "two", "content_digest": "b" * 64,
        "size": 1, "media_type": "application/json",
    })

    report = run_data_doctor(site_spec=site, catalog=other_catalog.path, package=package)

    package_inputs = next(check for check in report["checks"] if check["name"] == "package_inputs")
    assert package_inputs == {
        "name": "package_inputs", "passed": False,
        "message": "package does not match supplied catalog",
    }
    assert report["passed"] is False

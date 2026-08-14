import io

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.doctor import run_data_doctor
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

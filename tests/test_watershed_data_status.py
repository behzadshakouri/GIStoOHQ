import io
import json

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.package import freeze_package
from ohqbuilder.watershed_data.status import build_data_status, write_data_status
from ohqbuilder.watershed_data.workflow import write_site_spec


def test_status_lists_asset_ids_counts_and_object_availability(tmp_path):
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(b"native"))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "example", "product": "weather", "processing_status": "native",
        "content_digest": stored.content_digest, "size": stored.size, "media_type": "text/plain",
        "acquisition_attempts": 2,
        "variables": ["precipitation"],
        "issue_time_coverage": {"start": "2025-01-01T00:00:00Z", "end": "2025-01-01T06:00:00Z"},
        "valid_time_coverage": {"start": "2025-01-01T06:00:00Z", "end": "2025-01-01T12:00:00Z"},
        "members_by_variable": {"precipitation": ["control"]},
        "locations_by_variable": {"precipitation": ["grid-1"]},
        "units_by_variable": {"precipitation": "mm"},
        "record_counts_by_variable": {"precipitation": 2},
    })
    catalog.register({
        "provider": "example", "product": "derived", "processing_status": "derived",
        "parent_asset_ids": [native["asset_id"]], "content_digest": "f" * 64,
        "size": 1, "media_type": "text/csv",
        "transformation_name": "test-transform", "transformation_version": "1.0",
        "transformation_parameters": {},
    })
    report = build_data_status(catalog=catalog.path, object_store=store.root)
    assert report["asset_count"] == 2
    assert report["native_asset_count"] == 1
    assert report["derived_asset_count"] == 1
    assert report["missing_object_count"] == 1
    assert report["assets"][0]["asset_id"].startswith("sha256:")
    native_status = next(item for item in report["assets"] if item["asset_id"] == native["asset_id"])
    assert native_status["acquisition_attempts"] == 2
    assert native_status["issue_time_coverage"]["start"] == "2025-01-01T00:00:00Z"
    assert native_status["valid_time_coverage"]["end"] == "2025-01-01T12:00:00Z"
    assert native_status["members_by_variable"] == {"precipitation": ["control"]}
    assert native_status["units_by_variable"] == {"precipitation": "mm"}
    assert native_status["record_counts_by_variable"] == {"precipitation": 2}

    output = write_data_status(
        catalog=catalog.path, object_store=store.root, output=tmp_path / "status"
    )
    assert json.loads(output.read_text())["asset_count"] == 2
    assert native["asset_id"] in (tmp_path / "status" / "status.md").read_text()
    markdown = (tmp_path / "status" / "status.md").read_text()
    assert "| Attempts |" in markdown
    assert "## Forecast support" in markdown
    assert "2025-01-01T00:00:00Z → 2025-01-01T06:00:00Z" in markdown
    assert "precipitation" in markdown

    site = write_site_spec(
        tmp_path / "site.yaml", site_id="status", name="Status", longitude=-77,
        latitude=39, start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    package = tmp_path / "package"
    freeze_package(site_spec=site, catalog=catalog.path, output=package)
    packaged = build_data_status(
        catalog=catalog.path, object_store=store.root, package=package,
    )
    assert packaged["schema_version"] == "1.1"
    assert packaged["package_id"].startswith("sha256:")
    assert packaged["package_qc_status"] == "not_run"

import io
import json

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.status import build_data_status, write_data_status


def test_status_lists_asset_ids_counts_and_object_availability(tmp_path):
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(b"native"))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "example", "product": "weather", "processing_status": "native",
        "content_digest": stored.content_digest, "size": stored.size, "media_type": "text/plain",
    })
    catalog.register({
        "provider": "example", "product": "derived", "processing_status": "derived",
        "parent_asset_ids": [native["asset_id"]], "content_digest": "f" * 64,
        "size": 1, "media_type": "text/csv",
    })
    report = build_data_status(catalog=catalog.path, object_store=store.root)
    assert report["asset_count"] == 2
    assert report["native_asset_count"] == 1
    assert report["derived_asset_count"] == 1
    assert report["missing_object_count"] == 1
    assert report["assets"][0]["asset_id"].startswith("sha256:")

    output = write_data_status(
        catalog=catalog.path, object_store=store.root, output=tmp_path / "status"
    )
    assert json.loads(output.read_text())["asset_count"] == 2
    assert native["asset_id"] in (tmp_path / "status" / "status.md").read_text()

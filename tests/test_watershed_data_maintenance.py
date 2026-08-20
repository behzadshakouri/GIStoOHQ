import io
import json

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.maintenance import collect_unreferenced_objects


def test_garbage_collection_is_dry_run_and_preserves_referenced_objects(tmp_path):
    store = ObjectStore(tmp_path / "store")
    kept = store.put(io.BytesIO(b"kept"))
    orphan = store.put(io.BytesIO(b"orphan"))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    catalog.register({
        "provider": "example", "product": "weather", "content_digest": kept.content_digest,
        "size": kept.size, "media_type": "application/json",
    })
    output = tmp_path / "gc.json"
    report = collect_unreferenced_objects(
        object_store=store.root, catalogs=[catalog.path], output=output,
    )
    assert report["candidate_count"] == 1
    assert report["objects"][0]["content_digest"] == orphan.content_digest
    assert orphan.path.is_file()
    assert json.loads(output.read_text())["removed_count"] == 0

    deleted = collect_unreferenced_objects(
        object_store=store.root, catalogs=[catalog.path], delete=True,
    )
    assert deleted["removed_count"] == 1
    assert not orphan.path.exists()
    assert kept.path.is_file()

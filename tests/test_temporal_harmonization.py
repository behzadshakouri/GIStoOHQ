import csv
import io
import json
from pathlib import Path

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.temporal import harmonize_asset


def _native_asset(tmp_path, provider, fixture, product):
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(Path(fixture).read_bytes()))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    return store, catalog, catalog.register({
        "provider": provider, "product": product, "content_digest": stored.content_digest,
        "size": stored.size, "media_type": "application/json",
    })


def test_power_harmonization_creates_new_asset_qc_and_provenance(tmp_path):
    store, catalog, native = _native_asset(
        tmp_path, "nasa-power", "tests/fixtures/nasa_power_hourly.json", "historical-meteorology"
    )
    output = harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    assert output["processing_status"] == "derived"
    assert output["parent_asset_ids"] == [native["asset_id"]]
    with store.open(output["content_digest"]) as stream:
        rows = list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")))
    assert rows[0]["timestamp_utc"] == "2025-01-01T00:00:00Z"
    assert {row["variable"] for row in rows} == {"PRECTOTCORR", "T2M"}
    qc = json.loads((tmp_path / "qc.json").read_text())
    assert {item["rule_id"] for item in qc["results"]} == {
        "temporal.duplicate_timestamps", "temporal.missing_values", "temporal.chronology"
    }
    provenance = json.loads((tmp_path / "provenance.json").read_text())
    assert provenance["parent_asset_ids"] == [native["asset_id"]]
    assert provenance["output_asset_ids"] == [output["asset_id"]]


def test_usgs_harmonization_preserves_qualifiers_and_native_units(tmp_path):
    store, catalog, native = _native_asset(
        tmp_path, "usgs", "tests/fixtures/usgs_discharge.json", "observed-discharge"
    )
    output = harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    with store.open(output["content_digest"]) as stream:
        text = stream.read().decode()
    assert "ft3/s" in text
    assert ",P\n" in text
    assert "2025-01-01T05:00:00Z" in text


def test_daily_power_harmonization_preserves_daily_support(tmp_path):
    store, catalog, native = _native_asset(
        tmp_path, "nasa-power", "tests/fixtures/nasa_power_daily.json", "pet-et"
    )
    output = harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    with store.open(output["content_digest"]) as stream:
        text = stream.read().decode()
    assert "2025-01-01T00:00:00Z,EVPTRNS,1.2,mm/day" in text
    assert output["product"] == "harmonized-temporal-observations"

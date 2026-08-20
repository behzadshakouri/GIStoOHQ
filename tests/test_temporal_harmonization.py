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
    assert output["transformation_name"] == "native-to-utc-table"
    assert output["transformation_parameters"]["unit_conversion"] == "none"
    with store.open(output["content_digest"]) as stream:
        rows = list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")))
    assert rows[0]["timestamp_utc"] == "2025-01-01T00:00:00Z"
    assert {row["variable"] for row in rows} == {"PRECTOTCORR", "T2M"}
    qc = json.loads((tmp_path / "qc.json").read_text())
    assert {item["rule_id"] for item in qc["results"]} == {
        "temporal.duplicate_timestamps", "temporal.missing_values", "temporal.chronology",
        "temporal.physical_range",
        "temporal.expected_intervals",
        "temporal.unit_compatibility",
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


def test_temporal_qc_reports_impossible_provider_values(tmp_path):
    document = json.loads(Path("tests/fixtures/nasa_power_hourly.json").read_text())
    document["properties"]["parameter"]["RH2M"] = {"2025010100": 120.0}
    document["parameters"]["RH2M"] = {"units": "%"}
    raw = json.dumps(document).encode()
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "nasa-power", "product": "historical-meteorology",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json",
    })
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    qc = json.loads((tmp_path / "qc.json").read_text())
    result = next(item for item in qc["results"] if item["rule_id"] == "temporal.physical_range")
    assert result["passed"] is False
    assert result["severity"] == "error"
    assert result["details"]["violations"][0]["variable"] == "RH2M"


def test_temporal_qc_reports_missing_expected_hour(tmp_path):
    document = json.loads(Path("tests/fixtures/nasa_power_hourly.json").read_text())
    for values in document["properties"]["parameter"].values():
        values["2025010102"] = values.pop("2025010101")
    raw = json.dumps(document).encode()
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "nasa-power", "product": "historical-meteorology",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "temporal_resolution": "hourly",
    })
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    qc = json.loads((tmp_path / "qc.json").read_text())
    result = next(item for item in qc["results"] if item["rule_id"] == "temporal.expected_intervals")
    assert result["passed"] is False
    assert result["details"]["missing_interval_count"] == 2


def test_temporal_qc_rejects_incompatible_known_unit(tmp_path):
    document = json.loads(Path("tests/fixtures/nasa_power_hourly.json").read_text())
    document["parameters"]["T2M"]["units"] = "kelvin"
    raw = json.dumps(document).encode()
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "nasa-power", "product": "historical-meteorology",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "temporal_resolution": "hourly",
    })
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    qc = json.loads((tmp_path / "qc.json").read_text())
    result = next(item for item in qc["results"] if item["rule_id"] == "temporal.unit_compatibility")
    assert result["passed"] is False
    assert result["details"]["mismatches"][0] == {
        "actual_unit": "kelvin", "allowed_units": ["C"], "variable": "T2M",
    }

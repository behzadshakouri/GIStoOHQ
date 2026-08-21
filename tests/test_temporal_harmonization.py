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
        "temporal.provider_qualifiers",
        "temporal.study_period_coverage",
        "temporal.timestep_alignment",
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
    qc = json.loads((tmp_path / "qc.json").read_text())
    qualifier_result = next(
        item for item in qc["results"] if item["rule_id"] == "temporal.provider_qualifiers"
    )
    assert qualifier_result["passed"] is False
    assert qualifier_result["details"]["provisional_record_count"] == 1
    assert qualifier_result["details"]["qualifier_counts"] == {"A": 1, "P": 1}


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


def test_temporal_qc_reports_missing_value_completeness_by_variable(tmp_path):
    document = json.loads(Path("tests/fixtures/nasa_power_hourly.json").read_text())
    document["properties"]["parameter"]["T2M"]["2025010100"] = -999
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
    result = next(
        item for item in json.loads((tmp_path / "qc.json").read_text())["results"]
        if item["rule_id"] == "temporal.missing_values"
    )
    assert result["passed"] is False
    assert result["details"]["completeness_by_variable"] == {
        "PRECTOTCORR": {
            "record_count": 2, "valid_count": 2, "missing_count": 0,
            "missing_fraction": 0.0,
        },
        "T2M": {
            "record_count": 2, "valid_count": 1, "missing_count": 1,
            "missing_fraction": 0.5,
        },
    }
    assert result["details"]["examples"] == [{
        "timestamp": "2025-01-01T00:00:00+00:00", "variable": "T2M",
    }]


def test_temporal_qc_reports_duplicate_timestamp_examples(tmp_path):
    document = json.loads(Path("tests/fixtures/usgs_discharge.json").read_text())
    observations = document["value"]["timeSeries"][0]["values"][0]["value"]
    observations.append(dict(observations[0]))
    raw = json.dumps(document).encode()
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "usgs", "product": "observed-discharge",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json",
    })
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    result = next(
        item for item in json.loads((tmp_path / "qc.json").read_text())["results"]
        if item["rule_id"] == "temporal.duplicate_timestamps"
    )
    assert result["passed"] is False
    assert result["details"] == {
        "duplicate_count": 1,
        "examples": [{
            "timestamp": "2025-01-01T05:00:00+00:00", "variable": "00060",
        }],
    }


def test_temporal_qc_checks_chronology_within_each_variable(tmp_path):
    document = json.loads(Path("tests/fixtures/nasa_power_hourly.json").read_text())
    precipitation = document["properties"]["parameter"]["PRECTOTCORR"]
    document["properties"]["parameter"]["PRECTOTCORR"] = {
        "2025010101": precipitation["2025010101"],
        "2025010100": precipitation["2025010100"],
    }
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
    result = next(
        item for item in json.loads((tmp_path / "qc.json").read_text())["results"]
        if item["rule_id"] == "temporal.chronology"
    )
    assert result["passed"] is False
    assert result["details"] == {
        "inversion_count": 1,
        "examples": [{
            "variable": "PRECTOTCORR",
            "previous_timestamp": "2025-01-01T01:00:00+00:00",
            "current_timestamp": "2025-01-01T00:00:00+00:00",
        }],
    }


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


def test_temporal_qc_reports_observations_off_the_declared_time_grid(tmp_path):
    document = json.loads(Path("tests/fixtures/usgs_discharge.json").read_text())
    observations = document["value"]["timeSeries"][0]["values"][0]["value"]
    observations[0]["dateTime"] = "2025-01-01T00:15:00Z"
    observations[1]["dateTime"] = "2025-01-01T01:00:00Z"
    raw = json.dumps(document).encode()
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    native = catalog.register({
        "provider": "usgs", "product": "observed-discharge",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "temporal_resolution": "hourly",
    })
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
    )
    result = next(
        item for item in json.loads((tmp_path / "qc.json").read_text())["results"]
        if item["rule_id"] == "temporal.timestep_alignment"
    )
    assert result["passed"] is False
    assert result["details"]["misaligned_record_count"] == 1
    assert result["details"]["examples"] == [{
        "timestamp": "2025-01-01T00:15:00+00:00", "variable": "00060",
    }]


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


def test_temporal_qc_reports_incomplete_requested_study_period(tmp_path):
    store, catalog, native = _native_asset(
        tmp_path, "nasa-power", "tests/fixtures/nasa_power_hourly.json",
        "historical-meteorology",
    )
    harmonize_asset(
        asset_id=native["asset_id"], catalog=catalog.path, object_store=store.root,
        qc_output=tmp_path / "qc.json", provenance_output=tmp_path / "provenance.json",
        expected_start="2024-12-31T23:00:00Z", expected_end="2025-01-01T04:00:00Z",
    )
    qc = json.loads((tmp_path / "qc.json").read_text())
    result = next(
        item for item in qc["results"] if item["rule_id"] == "temporal.study_period_coverage"
    )
    assert result["passed"] is False
    assert result["severity"] == "warning"
    assert [gap["boundary"] for gap in result["details"]["uncovered_boundaries"]] == [
        "start", "end", "start", "end",
    ]


def test_temporal_qc_checks_study_period_coverage_for_each_variable(tmp_path):
    document = json.loads(Path("tests/fixtures/nasa_power_hourly.json").read_text())
    document["properties"]["parameter"]["T2M"].pop("2025010100")
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
        expected_start="2025-01-01T00:00:00Z", expected_end="2025-01-01T02:00:00Z",
    )
    result = next(
        item for item in json.loads((tmp_path / "qc.json").read_text())["results"]
        if item["rule_id"] == "temporal.study_period_coverage"
    )
    assert result["passed"] is False
    assert result["details"]["uncovered_boundaries"] == [{
        "variable": "T2M", "boundary": "start",
        "requested": "2025-01-01T00:00:00+00:00",
        "observed": "2025-01-01T01:00:00+00:00", "gap_seconds": 3600.0,
    }]

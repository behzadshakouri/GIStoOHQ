import csv
import io
import json
from pathlib import Path

import pytest

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.forecast import (
    FORECAST_VALIDATION_POLICY,
    acquire_forecast_archive,
    materialize_available_forecasts,
    validate_forecast_records,
)
from ohqbuilder.watershed_data.schemas import WatershedDataError


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return self.body


def test_forecast_contract_rejects_bad_lead_and_future_issue():
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    assert validate_forecast_records(records)["record_count"] == 2
    records[0]["lead_time_hours"] = 5
    with pytest.raises(WatershedDataError, match="inconsistent"):
        validate_forecast_records(records)

def test_forecast_contract_rejects_duplicate_and_nonfinite_records():
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records.append(dict(records[0]))
    with pytest.raises(WatershedDataError, match="duplicates a forecast key"):
        validate_forecast_records(records)


def test_forecast_contract_rejects_inconsistent_units_per_variable():
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[1]["variable"] = records[0]["variable"]
    records[1]["units"] = "inches"
    with pytest.raises(WatershedDataError, match=r"inconsistent units: precipitation=inches,mm"):
        validate_forecast_records(records)

    records[1]["units"] = records[0]["units"]
    summary = validate_forecast_records(records)
    assert summary["units_by_variable"] == {"precipitation": "mm"}

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["value"] = float("nan")
    with pytest.raises(WatershedDataError, match="non-finite"):
        validate_forecast_records(records)


def test_forecast_contract_rejects_empty_dimensions_and_nonnumeric_values():
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["member"] = " "
    with pytest.raises(WatershedDataError, match="invalid string fields: member"):
        validate_forecast_records(records)

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["units"] = None
    with pytest.raises(WatershedDataError, match="invalid string fields: units"):
        validate_forecast_records(records)

    with pytest.raises(WatershedDataError, match="record 0 must be an object"):
        validate_forecast_records(["not-an-object"])

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["value"] = "not-a-number"
    with pytest.raises(WatershedDataError, match="must be JSON numbers"):
        validate_forecast_records(records)

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["value"] = True
    with pytest.raises(WatershedDataError, match="must be JSON numbers"):
        validate_forecast_records(records)

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["lead_time_hours"] = "6"
    with pytest.raises(WatershedDataError, match="must be JSON numbers"):
        validate_forecast_records(records)


def test_forecast_acquisition_and_leakage_safe_view(tmp_path):
    raw = Path("tests/fixtures/forecast_archive.json").read_bytes()
    asset = acquire_forecast_archive(
        url="https://example.test/archive.json", provider="example", product="forecast",
        cache=tmp_path / "store", catalog=tmp_path / "catalog.json",
        opener=lambda *args, **kwargs: Response(raw),
    )
    assert asset["product_version"] == "forecast-records-v2"
    assert asset["validation_policy_version"] == "forecast-validation-v1"
    assert len(asset["validation_policy_digest"]) == 64
    assert asset["temporal_dimensions"] == ["issue_time", "valid_time", "lead_time_hours", "member"]
    view = materialize_available_forecasts(
        asset_id=asset["asset_id"], prediction_time="2025-01-01T03:00:00Z",
        catalog=tmp_path / "catalog.json", object_store=tmp_path / "store",
    )
    assert view["record_count"] == 1
    assert view["transformation_name"] == "prediction-time-availability-filter"
    assert view["product_version"] == "1.3"
    assert view["transformation_version"] == "1.3"
    assert view["transformation_parameters"]["timestamp_normalization"] == "UTC"
    assert view["transformation_parameters"]["numeric_normalization"] == "float"
    assert view["transformation_parameters"]["validation_policy_version"] == (
        "forecast-validation-v1"
    )
    assert view["transformation_parameters"]["validation_policy_digest"] == (
        asset["validation_policy_digest"]
    )
    assert FORECAST_VALIDATION_POLICY["lead_time_tolerance_hours"] == 1e-6
    assert view["variables"] == ["precipitation"]
    assert view["members"] == ["control"]
    assert view["units_by_variable"] == {"precipitation": "mm"}
    assert view["members_by_variable"] == {"precipitation": ["control"]}
    assert view["locations_by_variable"] == {"precipitation": ["grid-1"]}
    assert view["record_counts_by_variable"] == {"precipitation": 1}
    assert view["issue_time_coverage"] == {
        "start": "2025-01-01T00:00:00+00:00", "end": "2025-01-01T00:00:00+00:00",
    }
    with ObjectStore(tmp_path / "store").open(view["content_digest"]) as stream:
        rows = list(csv.DictReader(line.decode() for line in stream.readlines()))
    assert rows[0]["issue_time"] == "2025-01-01T00:00:00Z"


def test_forecast_acquisition_rejects_empty_identity_before_download(tmp_path):
    called = False

    def opener(*args, **kwargs):
        nonlocal called
        called = True

    with pytest.raises(WatershedDataError, match="provider and product must be non-empty"):
        acquire_forecast_archive(
            url="https://example.test/archive.json", provider=" ", product="forecast",
            cache=tmp_path / "store", catalog=tmp_path / "catalog.json", opener=opener,
        )
    assert called is False


def test_forecast_view_normalizes_timestamps_and_dimensions(tmp_path):
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["issue_time"] = "2024-12-31T19:00:00-05:00"
    records[0]["valid_time"] = "2025-01-01T01:00:00-05:00"
    records[0]["member"] = " member-1 "
    records[0]["provider_metadata"] = {"cycle": "00Z"}
    raw = json.dumps(records).encode()
    asset = acquire_forecast_archive(
        url="https://example.test/archive.json", provider="example", product="forecast",
        cache=tmp_path / "store", catalog=tmp_path / "catalog.json",
        opener=lambda *args, **kwargs: Response(raw),
    )
    view = materialize_available_forecasts(
        asset_id=asset["asset_id"], prediction_time="2025-01-01T03:00:00Z",
        catalog=tmp_path / "catalog.json", object_store=tmp_path / "store",
    )
    with ObjectStore(tmp_path / "store").open(view["content_digest"]) as stream:
        rows = list(csv.DictReader(line.decode() for line in stream.readlines()))
    assert rows[0]["issue_time"] == "2025-01-01T00:00:00Z"
    assert rows[0]["valid_time"] == "2025-01-01T06:00:00Z"
    assert rows[0]["member"] == "member-1"
    assert "provider_metadata" not in rows[0]


def test_forecast_view_digest_is_independent_of_native_record_order(tmp_path):
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    digests = []
    for name, ordered_records in (("forward", records), ("reverse", list(reversed(records)))):
        raw = json.dumps(ordered_records).encode()
        asset = acquire_forecast_archive(
            url=f"https://example.test/{name}.json", provider="example", product="forecast",
            cache=tmp_path / name / "store", catalog=tmp_path / name / "catalog.json",
            opener=lambda *args, body=raw, **kwargs: Response(body),
        )
        view = materialize_available_forecasts(
            asset_id=asset["asset_id"], prediction_time="2025-01-01T07:00:00Z",
            catalog=tmp_path / name / "catalog.json", object_store=tmp_path / name / "store",
        )
        digests.append(view["content_digest"])
    assert digests[0] == digests[1]


def test_forecast_view_digest_normalizes_integer_and_float_representation(tmp_path):
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    digests = []
    for name, value in (("integer", 1), ("float", 1.0)):
        records[0]["value"] = value
        raw = json.dumps(records).encode()
        asset = acquire_forecast_archive(
            url=f"https://example.test/{name}.json", provider="example", product="forecast",
            cache=tmp_path / name / "store", catalog=tmp_path / name / "catalog.json",
            opener=lambda *args, body=raw, **kwargs: Response(body),
        )
        view = materialize_available_forecasts(
            asset_id=asset["asset_id"], prediction_time="2025-01-01T03:00:00Z",
            catalog=tmp_path / name / "catalog.json", object_store=tmp_path / name / "store",
        )
        digests.append(view["content_digest"])
    assert digests[0] == digests[1]


def test_forecast_view_rejects_prediction_time_before_archive_availability(tmp_path):
    raw = Path("tests/fixtures/forecast_archive.json").read_bytes()
    asset = acquire_forecast_archive(
        url="https://example.test/archive.json", provider="example", product="forecast",
        cache=tmp_path / "store", catalog=tmp_path / "catalog.json",
        opener=lambda *args, **kwargs: Response(raw),
    )
    with pytest.raises(WatershedDataError, match="no records available"):
        materialize_available_forecasts(
            asset_id=asset["asset_id"], prediction_time="2024-12-31T23:00:00Z",
            catalog=tmp_path / "catalog.json", object_store=tmp_path / "store",
        )
    assert len(json.loads((tmp_path / "catalog.json").read_text())["assets"]) == 1


@pytest.mark.parametrize(
    ("raw", "message"),
    [(b"not-json", "not valid UTF-8 JSON"), (b'{}', "must contain a JSON array")],
)
def test_forecast_view_reports_invalid_source_documents(tmp_path, raw, message):
    store = ObjectStore(tmp_path / "store")
    stored = store.put(io.BytesIO(raw))
    catalog = AssetCatalog(tmp_path / "catalog.json")
    asset = catalog.register({
        "provider": "example", "product": "forecast",
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "processing_status": "native",
    })
    with pytest.raises(WatershedDataError, match=message):
        materialize_available_forecasts(
            asset_id=asset["asset_id"], prediction_time="2025-01-01T00:00:00Z",
            catalog=catalog.path, object_store=store.root,
        )

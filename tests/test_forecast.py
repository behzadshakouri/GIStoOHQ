import csv
import json
from pathlib import Path

import pytest

from ohqbuilder.watershed_data.catalog import ObjectStore
from ohqbuilder.watershed_data.forecast import (
    acquire_forecast_archive, materialize_available_forecasts, validate_forecast_records,
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

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["value"] = float("nan")
    with pytest.raises(WatershedDataError, match="non-finite"):
        validate_forecast_records(records)


def test_forecast_contract_rejects_empty_dimensions_and_nonnumeric_values():
    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["member"] = " "
    with pytest.raises(WatershedDataError, match="empty fields: member"):
        validate_forecast_records(records)

    with pytest.raises(WatershedDataError, match="record 0 must be an object"):
        validate_forecast_records(["not-an-object"])

    records = json.loads(Path("tests/fixtures/forecast_archive.json").read_text())
    records[0]["value"] = "not-a-number"
    with pytest.raises(WatershedDataError, match="must be numeric"):
        validate_forecast_records(records)


def test_forecast_acquisition_and_leakage_safe_view(tmp_path):
    raw = Path("tests/fixtures/forecast_archive.json").read_bytes()
    asset = acquire_forecast_archive(
        url="https://example.test/archive.json", provider="example", product="forecast",
        cache=tmp_path / "store", catalog=tmp_path / "catalog.json",
        opener=lambda *args, **kwargs: Response(raw),
    )
    assert asset["temporal_dimensions"] == ["issue_time", "valid_time", "lead_time_hours", "member"]
    view = materialize_available_forecasts(
        asset_id=asset["asset_id"], prediction_time="2025-01-01T03:00:00Z",
        catalog=tmp_path / "catalog.json", object_store=tmp_path / "store",
    )
    assert view["record_count"] == 1
    assert view["transformation_name"] == "prediction-time-availability-filter"
    with ObjectStore(tmp_path / "store").open(view["content_digest"]) as stream:
        rows = list(csv.DictReader(line.decode() for line in stream.readlines()))
    assert rows[0]["issue_time"] == "2025-01-01T00:00:00Z"


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

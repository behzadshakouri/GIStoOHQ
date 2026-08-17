from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ohqbuilder.watershed_data.catalog import ObjectStore
from ohqbuilder.watershed_data.nasa_power import (
    acquire_pet_et,
    acquire_historical_meteorology,
    build_meteorology_query,
    summarize_meteorology_json,
)
from ohqbuilder.watershed_data.schemas import SiteSpec, WatershedDataError


def _spec():
    return SiteSpec.from_dict({
        "site_id": "test", "geometry": {"outlet": {"longitude": -76.98, "latitude": 38.94}},
        "study_period": {"start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
    })


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


def test_power_query_uses_site_period_point_and_utc():
    endpoint, params = build_meteorology_query(_spec(), ("PRECTOTCORR", "T2M"))
    assert endpoint.endswith("/api/temporal/hourly/point")
    assert params["start"] == "20250101"
    assert params["time-standard"] == "UTC"
    assert params["parameters"] == "PRECTOTCORR,T2M"


def test_power_summary_preserves_native_variables_units_and_coverage():
    raw = Path("tests/fixtures/nasa_power_hourly.json").read_bytes()
    summary = summarize_meteorology_json(raw, ("PRECTOTCORR", "T2M"))
    assert summary["native_units"] == {"PRECTOTCORR": "mm/hour", "T2M": "C"}
    assert summary["temporal_coverage"] == {"start": "2025010100", "end": "2025010101"}
    with pytest.raises(WatershedDataError, match="missing variables"):
        summarize_meteorology_json(raw, ("RH2M",))


def test_power_acquisition_stores_exact_native_response(tmp_path):
    raw = Path("tests/fixtures/nasa_power_hourly.json").read_bytes()
    calls = []

    def opener(url, timeout):
        calls.append((url, timeout))
        return _Response(raw)

    asset = acquire_historical_meteorology(
        _spec(), cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
        parameters=("PRECTOTCORR", "T2M"), opener=opener,
    )
    query = parse_qs(urlsplit(calls[0][0]).query)
    assert query["parameters"] == ["PRECTOTCORR,T2M"]
    assert asset["processing_status"] == "native"
    assert asset["time_standard"] == "UTC"
    with ObjectStore(tmp_path / "cache").open(asset["content_digest"]) as stored:
        assert stored.read() == raw


def test_pet_et_acquisition_declares_provider_semantics(tmp_path):
    document = Path("tests/fixtures/nasa_power_daily.json").read_bytes()
    calls = []
    def opener(url, **kwargs):
        calls.append(url)
        return _Response(document)
    asset = acquire_pet_et(
        _spec(), cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
        opener=opener,
    )
    assert asset["product"] == "pet-et"
    assert asset["variable_semantics"] == "provider_evapotranspiration_parameter"
    assert asset["temporal_resolution"] == "daily"
    assert urlsplit(calls[0]).path.endswith("/api/temporal/daily/point")

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.schemas import SiteSpec, WatershedDataError
from ohqbuilder.watershed_data.usgs import (
    acquire_observed_discharge,
    build_discharge_query,
    summarize_discharge_json,
)


def _spec():
    return SiteSpec.from_dict({
        "site_id": "hickey_run",
        "geometry": {"outlet": {"longitude": -76.98, "latitude": 38.94}},
        "study_period": {"start": "2025-01-01T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        "sources": {"discharge": {"selection": "explicit"}},
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


def test_discharge_query_requires_explicit_numeric_station():
    endpoint, parameters = build_discharge_query(_spec(), "01649500")
    assert endpoint == "https://waterservices.usgs.gov/nwis/iv/"
    assert parameters["parameterCd"] == "00060"
    assert parameters["startDT"] == "2025-01-01T00:00:00Z"
    with pytest.raises(WatershedDataError, match="digits only"):
        build_discharge_query(_spec(), "nearest")


def test_native_discharge_summary_preserves_units_qualifiers_and_offsets():
    raw = Path("tests/fixtures/usgs_discharge.json").read_bytes()
    summary = summarize_discharge_json(raw, "01649500")
    assert summary["native_units"] == ["ft3/s"]
    assert summary["qualifiers"] == ["A", "P"]
    assert summary["observation_count"] == 2
    assert summary["temporal_coverage"]["start"].endswith("-05:00")


def test_acquisition_stores_exact_raw_response_and_catalogs_native_metadata(tmp_path):
    raw = Path("tests/fixtures/usgs_discharge.json").read_bytes()
    calls = []

    def opener(url, timeout):
        calls.append((url, timeout))
        return _Response(raw)

    asset = acquire_observed_discharge(
        _spec(), "01649500", cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
        opener=opener,
    )
    query = parse_qs(urlsplit(calls[0][0]).query)
    assert query["sites"] == ["01649500"]
    assert query["parameterCd"] == ["00060"]
    assert calls[0][1] == 120.0
    assert asset["processing_status"] == "native"
    assert asset["native_units"] == ["ft3/s"]
    assert asset["qualifiers"] == ["A", "P"]
    with ObjectStore(tmp_path / "cache").open(asset["content_digest"]) as stored:
        assert stored.read() == raw
    assert AssetCatalog(tmp_path / "catalog.json").read()["assets"][0]["station_id"] == "01649500"
    reused = acquire_observed_discharge(
        _spec(), "01649500", cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
        opener=lambda *args, **kwargs: pytest.fail("reusable request contacted provider"),
    )
    assert reused["asset_id"] == asset["asset_id"]


def test_acquisition_rejects_valid_json_without_requested_discharge(tmp_path):
    document = json.loads(Path("tests/fixtures/usgs_discharge.json").read_text())
    document["value"]["timeSeries"][0]["variable"]["variableCode"][0]["value"] = "00065"
    body = json.dumps(document).encode()
    with pytest.raises(WatershedDataError, match="no discharge series"):
        acquire_observed_discharge(
            _spec(), "01649500", cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
            opener=lambda *args, **kwargs: _Response(body),
        )
    assert not (tmp_path / "catalog.json").exists()


def test_acquisition_retries_transient_provider_failure(tmp_path, monkeypatch):
    raw = Path("tests/fixtures/usgs_discharge.json").read_bytes()
    calls = []

    def opener(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("temporary outage")
        return _Response(raw)

    monkeypatch.setattr("ohqbuilder.watershed_data.network.time.sleep", lambda delay: None)
    asset = acquire_observed_discharge(
        _spec(), "01649500", cache=tmp_path / "cache", catalog=tmp_path / "catalog.json",
        opener=opener,
    )
    assert len(calls) == 2
    assert asset["observation_count"] == 2

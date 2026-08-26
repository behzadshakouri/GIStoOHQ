from pathlib import Path
from urllib.parse import parse_qs, urlparse

import json
import pytest

import yaml

from ohqbuilder.watershed_data.reconnaissance import (
    run_reconnaissance, selected_station_from_report,
)
from ohqbuilder.watershed_data.schemas import SiteSpec, WatershedDataError
from ohqbuilder.watershed_data.usgs import (
    build_series_catalog_query, build_site_query, discover_gauges, parse_site_rdb,
)


def _spec():
    return SiteSpec.from_dict({
        "site_id": "hickey_run", "name": "Hickey Run",
        "geometry": {"outlet": {"longitude": -76.98, "latitude": 38.94}},
        "study_period": {"start": "2018-01-01T00:00:00Z", "end": "2025-12-31T23:00:00Z"},
        "sources": {"discharge": {"selection": "auto", "constraints": {
            "maximum_distance_km": 50, "require_study_period_overlap": True,
        }}},
    })


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


def test_usgs_site_query_and_rdb_parsing_are_deterministic():
    spec = _spec()
    url = build_site_query(spec, 25)
    assert url.startswith("https://waterservices.usgs.gov/nwis/site/")
    assert "parameterCd=00060" in url
    query = parse_qs(urlparse(url).query)
    assert "seriesCatalogOutput" not in query
    coordinates = query["bBox"][0].split(",")
    assert len(coordinates) == 4
    assert all(len(value.partition(".")[2]) == 6 for value in coordinates)
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), spec)
    assert [candidate.station_id for candidate in candidates][:2] == ["01649500", "01651000"]
    assert round(candidates[0].drainage_area_km2, 3) == 188.551
    assert candidates[0].record_start == "1938-10-01"


def test_usgs_site_query_rejects_invalid_radius():
    with pytest.raises(WatershedDataError, match="positive and finite"):
        build_site_query(_spec(), 0)


def test_usgs_series_catalog_query_is_bounded_and_uses_station_ids():
    url = build_series_catalog_query(["01649500", "01651000"])
    query = parse_qs(urlparse(url).query)

    assert query["seriesCatalogOutput"] == ["true"]
    assert query["sites"] == ["01649500,01651000"]
    with pytest.raises(WatershedDataError, match="1-25"):
        build_series_catalog_query([])


def test_usgs_series_catalog_rows_are_merged_per_station():
    fixture = Path("tests/fixtures/usgs_sites.rdb").read_text()
    duplicate = fixture.splitlines()[3].replace("1938-10-01", "2001-01-01")
    candidates = parse_site_rdb(fixture + duplicate + "\n", _spec())

    assert [candidate.station_id for candidate in candidates].count("01649500") == 1
    merged = next(candidate for candidate in candidates if candidate.station_id == "01649500")
    assert merged.record_start == "1938-10-01"


def test_usgs_discovery_fetches_coverage_for_nearest_candidates():
    full = Path("tests/fixtures/usgs_sites.rdb").read_bytes()
    calls = []

    def opener(url, **kwargs):
        calls.append(url)
        return _Response(full)

    query_url, candidates = discover_gauges(_spec(), radius_km=25, opener=opener)

    assert len(calls) == 2
    assert "bBox=" in calls[0]
    assert "seriesCatalogOutput=true" in calls[1]
    assert query_url == calls[0]
    assert candidates[0].record_start == "1938-10-01"


def test_reconnaissance_writes_json_and_markdown_and_selects_clear_best(tmp_path):
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump(_spec().to_dict()))
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), _spec())

    def discover(spec, *, radius_km):
        return "https://example.test/query", candidates

    report = run_reconnaissance(site, tmp_path / "recon", discover=discover)
    assert report["decision"] == "selected"
    assert report["selected_station_id"] == "01649500"
    assert report["candidates"][-1]["acceptable"] is False
    assert "record does not overlap" in report["candidates"][-1]["rejection_reasons"][0]
    assert (tmp_path / "recon" / "report.json").is_file()
    assert "01649500" in (tmp_path / "recon" / "report.md").read_text()


def test_required_topology_prevents_silent_selection(tmp_path):
    data = _spec().to_dict()
    data["sources"]["discharge"]["constraints"]["require_topological_compatibility"] = True
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump(data))
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), _spec())
    report = run_reconnaissance(
        site, tmp_path / "recon", discover=lambda spec, radius_km: ("query", candidates)
    )
    assert report["decision"] == "no_acceptable_candidate"
    assert all(not candidate["acceptable"] for candidate in report["candidates"])


def test_allowed_status_constraint_is_reported(tmp_path):
    data = _spec().to_dict()
    data["sources"]["discharge"]["constraints"]["allowed_statuses"] = ["inactive"]
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump(data))
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), _spec())[:1]
    report = run_reconnaissance(
        site, tmp_path / "recon", discover=lambda spec, radius_km: ("query", candidates)
    )
    assessment = report["candidates"][0]
    assert assessment["constraints"]["allowed_status"] is False
    assert "not allowed" in assessment["rejection_reasons"][0]


def test_selected_station_is_loaded_only_from_unambiguous_report(tmp_path):
    report = {
        "schema_name": "ReconnaissanceReport", "schema_version": "1.0",
        "decision": "selected", "selected_station_id": "01649500",
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    assert selected_station_from_report(tmp_path) == "01649500"
    report["decision"] = "ambiguous_candidates"
    report["selected_station_id"] = None
    (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(WatershedDataError, match="no unambiguous selection"):
        selected_station_from_report(tmp_path)

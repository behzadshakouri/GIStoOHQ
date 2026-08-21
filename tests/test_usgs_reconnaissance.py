from pathlib import Path

import json
import pytest

import yaml

from ohqbuilder.watershed_data.reconnaissance import (
    run_reconnaissance, selected_station_from_report,
)
from ohqbuilder.watershed_data.schemas import SiteSpec, WatershedDataError
from ohqbuilder.watershed_data.usgs import build_site_query, parse_site_rdb


def _spec():
    return SiteSpec.from_dict({
        "site_id": "hickey_run", "name": "Hickey Run",
        "geometry": {"outlet": {"longitude": -76.98, "latitude": 38.94}},
        "study_period": {"start": "2018-01-01T00:00:00Z", "end": "2025-12-31T23:00:00Z"},
        "sources": {"discharge": {"selection": "auto", "constraints": {
            "maximum_distance_km": 50, "require_study_period_overlap": True,
        }}},
    })


def test_usgs_site_query_and_rdb_parsing_are_deterministic():
    spec = _spec()
    url = build_site_query(spec, 25)
    assert url.startswith("https://waterservices.usgs.gov/nwis/site/")
    assert "parameterCd=00060" in url
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), spec)
    assert [candidate.station_id for candidate in candidates][:2] == ["01649500", "01651000"]
    assert round(candidates[0].drainage_area_km2, 3) == 188.551
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


def test_declared_topology_evidence_allows_only_compatible_station(tmp_path):
    data = _spec().to_dict()
    constraints = data["sources"]["discharge"]["constraints"]
    constraints["require_topological_compatibility"] = True
    constraints["topologically_compatible_station_ids"] = ["01649500"]
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump(data))
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), _spec())[:2]
    report = run_reconnaissance(
        site, tmp_path / "recon", discover=lambda spec, radius_km: ("query", candidates)
    )
    assert report["decision"] == "selected"
    assert report["selected_station_id"] == "01649500"
    assert report["candidates"][0]["constraints"]["topological_compatibility"] is True
    assert report["candidates"][1]["constraints"]["topological_compatibility"] is False


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


def test_drainage_area_constraint_rejects_incompatible_gauge(tmp_path):
    data = _spec().to_dict()
    constraints = data["sources"]["discharge"]["constraints"]
    constraints["expected_drainage_area_km2"] = 190.0
    constraints["maximum_drainage_area_error_fraction"] = 0.05
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump(data))
    candidates = parse_site_rdb(Path("tests/fixtures/usgs_sites.rdb").read_text(), _spec())[:2]
    report = run_reconnaissance(
        site, tmp_path / "recon", discover=lambda spec, radius_km: ("query", candidates)
    )
    first, second = report["candidates"]
    assert first["constraints"]["drainage_area_compatibility"] is True
    assert first["metrics"]["drainage_area_error_fraction"] < 0.05
    assert second["constraints"]["drainage_area_compatibility"] is False
    assert "drainage-area error" in second["rejection_reasons"][0]


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

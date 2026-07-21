import pytest

from ohqbuilder.watershed_bounds import (
    WatershedBoundsResult,
    expand_bounds,
    resolve_materialization_bounds,
    resolve_nldi_basin_bounds,
)


def test_expand_bounds_applies_scale_from_center():
    assert expand_bounds((0.0, 0.0, 10.0, 20.0), scale=1.1) == pytest.approx((-0.5, -1.0, 10.5, 21.0))


def test_resolve_nldi_basin_bounds_reads_comid_and_geometry(monkeypatch):
    responses = [
        {"features": [{"properties": {"identifier": "12345"}}]},
        {
            "features": [
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-77.1, 39.0], [-77.0, 39.0], [-77.0, 39.1], [-77.1, 39.0]]],
                    }
                }
            ]
        },
    ]
    urls = []

    def fake_load(url, *, timeout=20.0):
        urls.append(url)
        return responses.pop(0)

    monkeypatch.setattr("ohqbuilder.watershed_bounds._load_json", fake_load)

    result = resolve_nldi_basin_bounds(lon=-77.0, lat=39.0, safety_scale=1.0)

    assert result.bounds == (-77.1, 39.0, -77.0, 39.1)
    assert result.source == "nldi"
    assert "comid/position" in urls[0]
    assert "comid/12345/basin" in urls[1]


def test_resolve_materialization_bounds_falls_back_to_coordinate_buffer(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("ohqbuilder.watershed_bounds.resolve_nldi_basin_bounds", fail)

    result = resolve_materialization_bounds(lon=-77.0, lat=39.0, buffer_m=1000, prefer_web=True)

    assert isinstance(result, WatershedBoundsResult)
    assert result.source == "coordinate-buffer"
    assert result.bounds[0] < -77.0 < result.bounds[2]

import json

import pytest

from ohqbuilder.watershed_comparison import compare_watersheds


def test_compare_watersheds_reports_best_huc_and_disagreement(tmp_path):
    gpd = pytest.importorskip("geopandas")
    geometry = pytest.importorskip("shapely.geometry")
    generated = tmp_path / "generated.gpkg"
    reference = tmp_path / "reference.gpkg"
    gpd.GeoDataFrame(
        {"id": [1]}, geometry=[geometry.box(-77.05, 38.95, -77.0, 39.0)], crs="EPSG:4326"
    ).to_file(generated, layer="watershed", driver="GPKG")
    gpd.GeoDataFrame(
        {"huc12": ["near", "far"]},
        geometry=[
            geometry.box(-77.06, 38.94, -76.99, 39.01),
            geometry.box(-78.0, 38.0, -77.9, 38.1),
        ],
        crs="EPSG:4326",
    ).to_file(reference, layer="WBDHU12_reference", driver="GPKG")

    result = compare_watersheds(generated, reference, tmp_path / "comparison.json")

    payload = json.loads(result.read_text())
    assert payload["best_match"]["huc12"] == "near"
    assert 0 < payload["best_match"]["iou"] < 1
    assert payload["best_match"]["commission_area_km2"] > 0
    assert payload["best_match"]["omission_area_km2"] == pytest.approx(0.0, abs=1e-9)
    assert payload["measurement_crs"].startswith("EPSG:")

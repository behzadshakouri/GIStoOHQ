import json

import pytest

from ohqbuilder.watershed_comparison import WatershedComparisonError, compare_watersheds


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

    disagreement = tmp_path / "disagreement.gpkg"
    result = compare_watersheds(
        generated,
        reference,
        tmp_path / "comparison.json",
        disagreement_path=disagreement,
    )

    payload = json.loads(result.read_text())
    assert payload["best_match"]["reference_id"] == "near"
    assert 0 < payload["best_match"]["iou"] < 1
    assert payload["best_match"]["omission_area_km2"] > 0
    assert payload["best_match"]["commission_area_km2"] == pytest.approx(0.0, abs=1e-9)
    assert payload["measurement_crs"].startswith("EPSG:")
    assert payload["disagreement_geopackage"] == str(disagreement.resolve())
    import fiona

    assert set(fiona.listlayers(disagreement)) == {"intersection", "reference_only"}


def test_compare_watersheds_prioritizes_outlet_containing_huc_and_labels_scale(tmp_path):
    gpd = pytest.importorskip("geopandas")
    geometry = pytest.importorskip("shapely.geometry")
    generated = tmp_path / "generated.gpkg"
    reference = tmp_path / "reference.gpkg"
    gpd.GeoDataFrame(
        {"id": [1]}, geometry=[geometry.box(-77.05, 38.95, -77.0, 39.0)], crs="EPSG:4326"
    ).to_file(generated, layer="watershed", driver="GPKG")
    gpd.GeoDataFrame(
        {"huc12": ["contains-outlet", "higher-iou"]},
        geometry=[
            geometry.box(-77.1, 38.9, -76.9, 39.1),
            geometry.box(-77.04, 38.96, -77.0, 39.0),
        ],
        crs="EPSG:4326",
    ).to_file(reference, layer="WBDHU12_reference", driver="GPKG")

    result = compare_watersheds(
        generated,
        reference,
        tmp_path / "comparison.json",
        outlet_lon=-77.049,
        outlet_lat=38.951,
    )
    payload = json.loads(result.read_text())

    assert payload["selection_method"] == "highest_iou_among_outlet_containing_references"
    assert payload["best_match"]["reference_id"] == "contains-outlet"
    assert payload["best_match"]["contains_outlet"] is True
    assert payload["best_match"]["reference_scope"] == "regional_context_not_equivalent"


def test_compare_watersheds_requires_complete_outlet_coordinate_pair(tmp_path):
    with pytest.raises(WatershedComparisonError, match="Both outlet"):
        compare_watersheds("generated", "reference", tmp_path / "out.json", outlet_lon=-77.0)

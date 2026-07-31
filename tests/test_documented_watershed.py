import importlib.util
import json
from pathlib import Path

import pytest

from ohqbuilder.documented_watershed import (
    DocumentedWatershedError,
    REFERENCE_LAYER,
    import_documented_watershed,
)


GIS_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ("geopandas", "shapely")
)


def test_map_image_is_not_accepted_as_a_boundary(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    image = tmp_path / "published-map.png"
    image.write_bytes(b"not a polygon")

    with pytest.raises(DocumentedWatershedError, match="not a georeferenced polygon"):
        import_documented_watershed(
            image,
            tmp_path / "reference.gpkg",
            outlet_lon=-76.97,
            outlet_lat=38.95,
            source_title="Published Sligo Creek map",
            source_organization="Publisher",
        )


def test_import_local_named_boundary_records_provenance(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    import geopandas as gpd
    from shapely.geometry import box

    source = tmp_path / "county.gpkg"
    gpd.GeoDataFrame(
        {"BASIN": ["Sligo Creek", "Other"]},
        geometry=[box(-77.1, 38.8, -76.9, 39.1), box(-78, 38, -77.5, 38.5)],
        crs="EPSG:4326",
    ).to_file(source, layer="watersheds", driver="GPKG")

    result = import_documented_watershed(
        source,
        tmp_path / "DocumentedWatershed_reference.gpkg",
        layer="watersheds",
        name_field="BASIN",
        name="Sligo Creek",
        outlet_lon=-76.974,
        outlet_lat=38.957,
        source_title="County watershed inventory",
        source_organization="Example County",
        source_url="https://example.gov/watersheds",
        license_text="Public domain",
    )

    frame = gpd.read_file(result, layer=REFERENCE_LAYER)
    assert frame["BASIN"].tolist() == ["Sligo Creek"]
    assert frame["ref_kind"].tolist() == ["documented_named_watershed"]
    assert frame["ref_org"].tolist() == ["Example County"]
    metadata = json.loads(result.with_suffix(".json").read_text())
    assert metadata["selection_name"] == "Sligo Creek"
    assert metadata["feature_count"] == 1


def test_import_rejects_polygon_that_misses_outlet(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    import geopandas as gpd
    from shapely.geometry import box

    source = tmp_path / "wrong.geojson"
    gpd.GeoDataFrame(
        {"name": ["Wrong basin"]},
        geometry=[box(-78, 38, -77.5, 38.5)],
        crs="EPSG:4326",
    ).to_file(source, driver="GeoJSON")

    with pytest.raises(DocumentedWatershedError, match="contains the modeled outlet"):
        import_documented_watershed(
            source,
            tmp_path / "reference.gpkg",
            outlet_lon=-76.974,
            outlet_lat=38.957,
            source_title="Wrong inventory",
            source_organization="Example County",
        )

import importlib.util
import json
import pytest

from ohqbuilder.documented_watershed import (
    DocumentedWatershedError,
    REFERENCE_LAYER,
    export_boundary_vertices,
    import_documented_watershed,
)


def test_export_boundary_vertices_preserves_parts_holes_and_crs(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    import csv
    import geopandas as gpd
    from shapely.geometry import MultiPolygon, Polygon, box

    polygon_with_hole = Polygon(
        [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)],
        holes=[[(1, 1), (2, 1), (2, 2), (1, 2), (1, 1)]],
    )
    source = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame(
        geometry=[MultiPolygon([polygon_with_hole, box(5, 5, 6, 6)])], crs="EPSG:4326"
    ).to_file(source, layer="boundary", driver="GPKG")

    result = export_boundary_vertices(
        source, tmp_path / "vertices.csv", layer="boundary", target_crs="EPSG:26918"
    )

    with result.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 15
    assert {(row["part_id"], row["ring_type"]) for row in rows} == {
        ("0", "exterior"),
        ("0", "interior"),
        ("1", "exterior"),
    }
    assert float(rows[0]["x"]) > 100_000


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


def test_import_can_record_review_outline_that_misses_outlet(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    import geopandas as gpd
    from shapely.geometry import box

    source = tmp_path / "review.geojson"
    gpd.GeoDataFrame(
        {"name": ["Estimated outline"]},
        geometry=[box(-77.1, 39.0, -76.9, 39.1)],
        crs="EPSG:4326",
    ).to_file(source, driver="GeoJSON")

    result = import_documented_watershed(
        source,
        tmp_path / "reference.gpkg",
        outlet_lon=-76.974,
        outlet_lat=38.957,
        source_title="Estimated review outline",
        source_organization="Operator digitized review",
        require_outlet_containment=False,
    )

    metadata = json.loads(result.with_suffix(".json").read_text())
    assert metadata["outlet_containment_required"] is False
    assert metadata["outlet_containment_satisfied"] is False


def test_import_kmz_closed_line_as_derived_boundary(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    import geopandas as gpd
    from zipfile import ZipFile

    kmz = tmp_path / "estimated.kmz"
    kml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<kml xmlns=\"http://www.opengis.net/kml/2.2\"><Document><Placemark>
<name>Estimated SC</name><LineString><coordinates>
-77.1,38.9,0 -76.9,38.9,0 -76.9,39.1,0 -77.1,39.1,0 -77.1,38.9,0
</coordinates></LineString></Placemark></Document></kml>
"""
    with ZipFile(kmz, "w") as archive:
        archive.writestr("doc.kml", kml)

    result = import_documented_watershed(
        kmz,
        tmp_path / "DocumentedWatershed_reference.gpkg",
        outlet_lon=-77.0,
        outlet_lat=39.0,
        source_title="Estimated Sligo Creek review outline",
        source_organization="Operator digitized review",
    )

    frame = gpd.read_file(result, layer=REFERENCE_LAYER)
    assert frame.geometry.geom_type.tolist() == ["Polygon"]
    assert frame["name"].tolist() == ["Estimated SC"]
    assert frame["source_geometry"].tolist() == ["LineString"]
    assert frame["derived_from_closed_line"].tolist() == [True]
    metadata = json.loads(result.with_suffix(".json").read_text())
    assert metadata["source_dataset"].endswith("estimated.kmz")


def test_import_kmz_open_line_is_not_boundary(tmp_path):
    if not GIS_AVAILABLE:
        pytest.skip("GIS dependencies are not installed")
    from zipfile import ZipFile

    kmz = tmp_path / "open.kmz"
    kml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<kml xmlns=\"http://www.opengis.net/kml/2.2\"><Document><Placemark>
<name>Open line</name><LineString><coordinates>
-77.1,38.9,0 -76.9,38.9,0 -76.9,39.1,0
</coordinates></LineString></Placemark></Document></kml>
"""
    with ZipFile(kmz, "w") as archive:
        archive.writestr("doc.kml", kml)

    with pytest.raises(DocumentedWatershedError, match="Closed KML/KMZ lines"):
        import_documented_watershed(
            kmz,
            tmp_path / "reference.gpkg",
            outlet_lon=-77.0,
            outlet_lat=39.0,
            source_title="Open outline",
            source_organization="Operator digitized review",
        )

import builtins
import json

import pytest

from ohqbuilder.pour_points import PourPointGenerationError, generate_pour_points


def test_generate_pour_points_explains_missing_gis_dependency(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "geopandas":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(PourPointGenerationError, match=r"pip install -e .\[gis\]"):
        generate_pour_points(tmp_path / "junctions.gpkg", tmp_path / "pour_points.shp")


def test_generate_pour_points_uses_outlet_when_watershed_has_no_junctions(tmp_path):
    gpd = pytest.importorskip("geopandas")
    shapely_geometry = pytest.importorskip("shapely.geometry")

    junctions_path = tmp_path / "junctions.gpkg"
    outlet_path = tmp_path / "outlet.shp"
    output_path = tmp_path / "pour_points.shp"
    gpd.GeoDataFrame({"junction_id": []}, geometry=[], crs="EPSG:26918").to_file(
        junctions_path, layer="junctions", driver="GPKG"
    )
    gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[shapely_geometry.Point(328900, 4317700)],
        crs="EPSG:26918",
    ).to_file(outlet_path)

    result = generate_pour_points(junctions_path, output_path, fallback_outlet_path=outlet_path)

    generated = gpd.read_file(output_path)
    assert result.count == 1
    assert generated["id"].tolist() == [1]
    assert generated["name"].tolist() == ["WatershedOutlet"]
    assert generated["role"].tolist() == ["watershed_outlet"]
    assert result.report_path == tmp_path / "pour_points_generation_report.json"
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["method"] == "phase1_junctions_plus_watershed_outlet"
    assert report["count"] == 1
    assert report["points"][0]["id"] == 1
    assert report["points"][0]["role"] == "watershed_outlet"
    assert report["points"][0]["x"] == pytest.approx(328900)
    assert report["points"][0]["y"] == pytest.approx(4317700)


def test_generate_pour_points_appends_outlet_to_junctions(tmp_path):
    gpd = pytest.importorskip("geopandas")
    geometry = pytest.importorskip("shapely.geometry")
    junctions_path = tmp_path / "junctions.gpkg"
    outlet_path = tmp_path / "outlet.gpkg"
    output_path = tmp_path / "pour_points.shp"
    gpd.GeoDataFrame(
        {"junction_id": [1, 2]},
        geometry=[geometry.Point(0, 1), geometry.Point(0, 2)],
        crs="EPSG:26918",
    ).to_file(junctions_path, layer="junctions", driver="GPKG")
    gpd.GeoDataFrame(
        {"id": [1]}, geometry=[geometry.Point(0, 0)], crs="EPSG:26918"
    ).to_file(outlet_path, driver="GPKG")

    result = generate_pour_points(
        junctions_path, output_path, fallback_outlet_path=outlet_path
    )

    generated = gpd.read_file(result.output_path).sort_values("id")
    assert result.count == 3
    assert generated["role"].tolist() == ["junction", "junction", "watershed_outlet"]
    assert generated["name"].tolist()[-1] == "WatershedOutlet"

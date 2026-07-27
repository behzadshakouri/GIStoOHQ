import builtins

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
    assert generated["name"].tolist() == ["P1"]

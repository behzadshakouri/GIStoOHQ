import json

from ohqbuilder.cli import main
from ohqbuilder.dem_acquisition import create_outlet_buffer_area
from ohqbuilder.dem_materializer import read_dem_manifest


def test_create_oriented_outlet_buffer_area_writes_geojson(tmp_path):
    out = tmp_path / "dem_acquisition_area.geojson"

    result = create_outlet_buffer_area(
        -76.9765,
        38.9921,
        out,
        upstream_km=35,
        downstream_km=3,
        lateral_km=4,
        azimuth_deg=20,
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    props = data["features"][0]["properties"]
    assert result.mode == "oriented_outlet_buffer"
    assert props["mode"] == "oriented_outlet_buffer"
    assert props["area_km2"] == 304
    assert len(data["features"][0]["geometry"]["coordinates"][0]) == 5


def test_read_dem_manifest_uses_explicit_tiles(tmp_path):
    raw = tmp_path / "dem" / "raw"
    raw.mkdir(parents=True)
    tile = raw / "tile_01.tif"
    tile.write_text("fake", encoding="utf-8")
    manifest = tmp_path / "intermediate" / "dem_download_manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"tiles": ["../dem/raw/tile_01.tif"]}), encoding="utf-8")

    assert read_dem_manifest(manifest) == [tile.resolve()]


def test_cli_dem_acquisition_area(tmp_path, capsys):
    out = tmp_path / "area.geojson"

    assert main([
        "dem-acquisition-area",
        "--lon", "-76.9765",
        "--lat", "38.9921",
        "--out", str(out),
        "--upstream-km", "35",
        "--downstream-km", "3",
        "--lateral-km", "4",
        "--azimuth", "20",
    ]) == 0

    text = capsys.readouterr().out
    assert "Wrote acquisition area:" in text
    assert "oriented_outlet_buffer" in text
    assert out.exists()


def _feature(name, bounds, **props):
    minx, miny, maxx, maxy = bounds
    merged = {"name": name, **props}
    return {
        "type": "Feature",
        "properties": merged,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]
            ]],
        },
    }


def test_build_dem_tile_manifest_selects_intersecting_tiles(tmp_path):
    from ohqbuilder.dem_acquisition import build_dem_tile_manifest

    acquisition = tmp_path / "area.geojson"
    index = tmp_path / "tile_index.geojson"
    out = tmp_path / "manifest.json"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("area", (-77.1, 38.9, -76.9, 39.1))],
    }), encoding="utf-8")
    index.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            _feature("inside", (-77.0, 39.0, -76.95, 39.05), path="dem/raw/tile_01.tif", url="https://example.test/tile_01.tif"),
            _feature("outside", (-76.0, 39.0, -75.9, 39.1), path="dem/raw/tile_02.tif"),
        ],
    }), encoding="utf-8")

    result = build_dem_tile_manifest(acquisition, index, out)

    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert result.selected_count == 1
    assert manifest["tiles"] == ["dem/raw/tile_01.tif"]
    assert manifest["items"][0]["url"] == "https://example.test/tile_01.tif"


def test_cli_dem_tile_manifest(tmp_path, capsys):
    acquisition = tmp_path / "area.geojson"
    index = tmp_path / "tile_index.geojson"
    out = tmp_path / "manifest.json"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("area", (-77.1, 38.9, -76.9, 39.1))],
    }), encoding="utf-8")
    index.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("inside", (-77.0, 39.0, -76.95, 39.05), path="tile.tif")],
    }), encoding="utf-8")

    assert main([
        "dem-tile-manifest",
        "--acquisition-area", str(acquisition),
        "--tile-index", str(index),
        "--out", str(out),
    ]) == 0

    text = capsys.readouterr().out
    assert "Selected tile count: 1" in text
    assert out.exists()


def test_build_dem_tile_manifest_rejects_bbox_only_overlap(tmp_path):
    from ohqbuilder.dem_acquisition import build_dem_tile_manifest

    acquisition = tmp_path / "area.geojson"
    index = tmp_path / "tile_index.geojson"
    out = tmp_path / "manifest.json"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "upper_left"},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 1], [0, 2], [1, 2], [0, 1]]]},
        }],
    }), encoding="utf-8")
    index.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "lower_right", "path": "tile.tif"},
            "geometry": {"type": "Polygon", "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 0]]]},
        }],
    }), encoding="utf-8")

    result = build_dem_tile_manifest(acquisition, index, out)
    manifest = json.loads(out.read_text(encoding="utf-8"))

    assert result.selected_count == 0
    assert manifest["tiles"] == []

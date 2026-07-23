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

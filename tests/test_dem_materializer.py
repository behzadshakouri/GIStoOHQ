import zipfile

from ohqbuilder.cli import main
from ohqbuilder.dem_materializer import bounds_from_lonlat_buffer, discover_dem_sources, parse_bounds, utm_epsg_from_lonlat


def test_utm_epsg_from_lonlat():
    assert utm_epsg_from_lonlat(-111.2, 35.1) == 32612
    assert utm_epsg_from_lonlat(151.2, -33.8) == 32756


def test_bounds_from_lonlat_buffer_applies_safety_margin():
    minx, miny, maxx, maxy = bounds_from_lonlat_buffer(-77.0, 39.0, 10_000, scale=1.1)

    assert minx < -77.0 < maxx
    assert miny < 39.0 < maxy
    assert round(maxy - 39.0, 3) == 0.099


def test_parse_bounds_accepts_csv_string():
    assert parse_bounds("-77.2,38.9,-76.8,39.2") == (-77.2, 38.9, -76.8, 39.2)


def test_discover_dem_sources_finds_rasters_and_archives(tmp_path):
    root = tmp_path / "source_downloads"
    (root / "site" / "dem").mkdir(parents=True)
    tif = root / "site" / "dem" / "tile.tif"
    tif.write_text("not a real raster", encoding="utf-8")
    archive = root / "site" / "dem" / "tile.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/tile2.tif", "not a real raster")
    (root / "site" / "dem" / "notes.txt").write_text("ignore", encoding="utf-8")

    assert discover_dem_sources(root) == [tif, archive]


def test_cli_materialize_dem(monkeypatch, tmp_path, capsys):
    def fake_materialize(root, site, **kwargs):
        return type(
            "Result",
            (),
            {
                "output_path": tmp_path / "SITE_A" / "demlr" / "cliped_utm.tif",
                "source_count": 2,
                "dst_crs": "EPSG:32612",
            },
        )()

    monkeypatch.setattr("ohqbuilder.cli.materialize_dem", fake_materialize)

    assert main(["materialize-dem", "--root", str(tmp_path), "--site", "SITE_A"]) == 0
    out = capsys.readouterr().out
    assert "Wrote DEM:" in out
    assert "EPSG:32612" in out

import zipfile

from ohqbuilder.cli import main
from ohqbuilder.dem_materializer import discover_dem_sources, utm_epsg_from_lonlat


def test_utm_epsg_from_lonlat():
    assert utm_epsg_from_lonlat(-111.2, 35.1) == 32612
    assert utm_epsg_from_lonlat(151.2, -33.8) == 32756


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

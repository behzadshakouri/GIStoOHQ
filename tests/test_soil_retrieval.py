from pathlib import Path

from ohqbuilder.cli import main
from ohqbuilder.soil_retrieval import SoilRetrievalResult


def test_cli_download_hsg(monkeypatch, tmp_path, capsys):
    def fake_hsg(root, site, **kwargs):
        soils = Path(root) / site / "soils"
        return SoilRetrievalResult(soils, soils / "hydrologic_soil_groups.gpkg", (soils / "hsg.tif",), 1)

    monkeypatch.setattr("ohqbuilder.cli.retrieve_hydrologic_soil_groups", fake_hsg)

    assert main(["download-hsg", "--root", str(tmp_path), "--site", "SITE_A"]) == 0
    assert "Wrote HSG raster" in capsys.readouterr().out


def test_cli_download_texture(monkeypatch, tmp_path, capsys):
    def fake_texture(root, site, **kwargs):
        soils = Path(root) / site / "soils"
        return SoilRetrievalResult(soils, soils / "soil_texture.gpkg", (soils / "sand_pct.tif",), 1)

    monkeypatch.setattr("ohqbuilder.cli.retrieve_soil_texture", fake_texture)

    assert main(["download-texture", "--root", str(tmp_path), "--site", "SITE_A"]) == 0
    assert "Wrote texture raster" in capsys.readouterr().out

from pathlib import Path

from ohqbuilder.cli import main
from ohqbuilder.soil_retrieval import (
    SoilRetrievalResult,
    _resolve_hsg,
    _topsoil_by_mukey,
    _usda_texture,
    retrieve_hydrologic_soil_groups,
)


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


def test_hydrologic_soil_groups_delegates_to_soil_groups(monkeypatch, tmp_path):
    calls = []
    expected = SoilRetrievalResult(tmp_path / "SITE_A" / "soils", tmp_path / "hsg.gpkg", (tmp_path / "hsg.tif",), 7)

    def fake_soil_groups(root, site, **kwargs):
        calls.append((root, site, kwargs))
        return expected

    monkeypatch.setattr("ohqbuilder.soil_retrieval.retrieve_soil_groups", fake_soil_groups)

    assert retrieve_hydrologic_soil_groups(tmp_path, "SITE_A", buffer=123.0, pixel_size=0.5) == expected
    assert calls == [(tmp_path, "SITE_A", {"buffer": 123.0, "pixel_size": 0.5})]


def test_resolve_hsg_uses_conservative_dual_group_member():
    assert _resolve_hsg("A/D") == "D"
    assert _resolve_hsg(" b ") == "B"
    assert _resolve_hsg(None) == ""


def test_topsoil_by_mukey_uses_dominant_component_weighted_texture():
    rows = [
        {
            "mukey": "1",
            "cokey": "20",
            "comppct_r": 40,
            "compname": "minor",
            "hzdept_r": 0,
            "hzdepb_r": 30,
            "sandtotal_r": 90,
            "silttotal_r": 5,
            "claytotal_r": 5,
            "om_r": 1,
        },
        {
            "mukey": "1",
            "cokey": "10",
            "comppct_r": 60,
            "compname": "dominant",
            "hzdept_r": 0,
            "hzdepb_r": 10,
            "sandtotal_r": 40,
            "silttotal_r": 40,
            "claytotal_r": 20,
            "om_r": 2,
        },
        {
            "mukey": "1",
            "cokey": "10",
            "comppct_r": 60,
            "compname": "dominant",
            "hzdept_r": 10,
            "hzdepb_r": 30,
            "sandtotal_r": 20,
            "silttotal_r": 40,
            "claytotal_r": 40,
            "om_r": 4,
        },
    ]

    topsoil = _topsoil_by_mukey(rows, 30)["1"]

    assert topsoil["compname"] == "dominant"
    assert topsoil["comppct"] == 60
    assert round(topsoil["sand"], 2) == 26.67
    assert round(topsoil["silt"], 2) == 40.0
    assert round(topsoil["clay"], 2) == 33.33
    assert topsoil["texture"] == _usda_texture(topsoil["sand"], topsoil["silt"], topsoil["clay"])
    assert topsoil["texture_code"] == 8

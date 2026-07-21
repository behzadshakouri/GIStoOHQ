from pathlib import Path
from types import SimpleNamespace

import pytest

from ohqbuilder.cli import main
from ohqbuilder.source_materializer import find_product_dir, materialize_landcover, materialize_source_inputs


def test_materialize_source_inputs_combines_dem_and_hydro(monkeypatch, tmp_path):
    downloads = tmp_path / "SITE_A" / "source_downloads" / "source-id"
    (downloads / "demlr").mkdir(parents=True)
    (downloads / "hydro").mkdir()
    dem_path = tmp_path / "SITE_A" / "demlr" / "cliped_utm.tif"
    flowlines = tmp_path / "SITE_A" / "outputs" / "NHDFlowline_clip.gpkg"
    calls = []

    monkeypatch.setattr(
        "ohqbuilder.source_materializer.materialize_dem",
        lambda *args, **kwargs: calls.append(("dem", kwargs))
        or SimpleNamespace(output_path=dem_path),
    )
    monkeypatch.setattr(
        "ohqbuilder.source_materializer.materialize_flowlines",
        lambda *args, **kwargs: calls.append(("hydro", kwargs))
        or SimpleNamespace(output_path=flowlines),
    )

    result = materialize_source_inputs(
        tmp_path, "SITE_A", source_dir=downloads.parent, target_crs="EPSG:26912"
    )

    assert result.dem.output_path == dem_path
    assert result.hydro.output_path == flowlines
    assert calls[0][1]["source_dir"] == downloads / "demlr"
    assert calls[1][1]["source_dir"] == downloads / "hydro"
    assert calls[1][1]["dem_path"] == dem_path



def test_materialize_source_inputs_copies_nlcd_to_legacy_name(monkeypatch, tmp_path):
    downloads = tmp_path / "SITE_A" / "source_downloads" / "source-id"
    (downloads / "demlr").mkdir(parents=True)
    (downloads / "hydro").mkdir()
    landcover = downloads / "landcover"
    landcover.mkdir()
    source_nlcd = landcover / "nlcd_2023_SligoCreek_Mouth.tif"
    source_nlcd.write_bytes(b"nlcd")

    monkeypatch.setattr(
        "ohqbuilder.source_materializer.materialize_dem",
        lambda *args, **kwargs: SimpleNamespace(output_path=tmp_path / "dem.tif"),
    )
    monkeypatch.setattr(
        "ohqbuilder.source_materializer.materialize_flowlines",
        lambda *args, **kwargs: SimpleNamespace(output_path=tmp_path / "flowlines.gpkg"),
    )

    result = materialize_source_inputs(tmp_path, "SligoCreek", source_dir=downloads.parent)

    expected = tmp_path / "SligoCreek" / "landcover" / "nlcd_2023_SligoCreek.tif"
    assert result.landcover == expected
    assert expected.read_bytes() == b"nlcd"


def test_materialize_landcover_is_optional(tmp_path):
    assert materialize_landcover(tmp_path, "SITE_A", tmp_path) is None


def test_materialize_source_inputs_forwards_user_clip_bounds(monkeypatch, tmp_path):
    downloads = tmp_path / "SITE_A" / "source_downloads" / "source-id"
    (downloads / "demlr").mkdir(parents=True)
    (downloads / "hydro").mkdir()
    dem_path = tmp_path / "SITE_A" / "demlr" / "cliped_utm.tif"
    calls = []

    monkeypatch.setattr(
        "ohqbuilder.source_materializer.materialize_dem",
        lambda *args, **kwargs: calls.append(("dem", kwargs))
        or SimpleNamespace(output_path=dem_path),
    )
    monkeypatch.setattr(
        "ohqbuilder.source_materializer.materialize_flowlines",
        lambda *args, **kwargs: calls.append(("hydro", kwargs))
        or SimpleNamespace(output_path=tmp_path / "flowlines.gpkg"),
    )

    materialize_source_inputs(
        tmp_path,
        "SITE_A",
        source_dir=downloads.parent,
        clip_bounds="-77.2,38.9,-76.8,39.2",
    )

    assert calls[0][1]["clip_bounds"] == (-77.2, 38.9, -76.8, 39.2)
    assert calls[0][1]["clip_bounds_crs"] == "EPSG:4326"


def test_find_product_dir_rejects_ambiguous_sites(tmp_path):
    (tmp_path / "one" / "hydro").mkdir(parents=True)
    (tmp_path / "two" / "hydro").mkdir(parents=True)

    with pytest.raises(ValueError, match="Multiple downloaded hydro"):
        find_product_dir(tmp_path, "hydro")


def test_cli_materialize_inputs(monkeypatch, tmp_path, capsys):
    result = SimpleNamespace(
        dem=SimpleNamespace(output_path=Path("dem.tif")),
        hydro=SimpleNamespace(output_path=Path("flowlines.gpkg")),
    )
    calls = []

    def fake_materialize(*args, **kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr("ohqbuilder.cli.materialize_source_inputs", fake_materialize)

    status = main(
        [
            "materialize-inputs",
            "--root",
            str(tmp_path),
            "--site",
            "SITE_A",
            "--clip-center-lat",
            "39.0",
            "--clip-center-lon",
            "-77.0",
            "--clip-buffer",
            "20000",
        ]
    )

    assert status == 0
    assert calls[0]["clip_center_lat"] == 39.0
    assert calls[0]["clip_center_lon"] == -77.0
    assert calls[0]["clip_buffer_m"] == 20000
    output = capsys.readouterr().out
    assert "Wrote DEM: dem.tif" in output

from pathlib import Path
from types import SimpleNamespace

import pytest

from ohqbuilder.cli import main
from ohqbuilder.source_materializer import find_product_dir, materialize_source_inputs


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
    monkeypatch.setattr("ohqbuilder.cli.materialize_source_inputs", lambda *a, **k: result)

    status = main(
        ["materialize-inputs", "--root", str(tmp_path), "--site", "SITE_A"]
    )

    assert status == 0
    assert "Wrote DEM: dem.tif" in capsys.readouterr().out

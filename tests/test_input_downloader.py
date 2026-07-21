from types import SimpleNamespace

import pytest

from ohqbuilder.cli import main
from ohqbuilder.input_downloader import download_all_inputs


def test_download_all_inputs_runs_source_downloaders_from_coordinate(monkeypatch, tmp_path):
    calls = []
    phase1 = SimpleNamespace(download_dir=tmp_path / "source_downloads")
    hsg = SimpleNamespace(vector_path=tmp_path / "soils" / "hsg.gpkg")
    texture = SimpleNamespace(vector_path=tmp_path / "soils" / "texture.gpkg")

    monkeypatch.setattr(
        "ohqbuilder.input_downloader.fetch_phase1_inputs",
        lambda *args, **kwargs: calls.append(("phase1", kwargs)) or phase1,
    )
    monkeypatch.setattr(
        "ohqbuilder.input_downloader.retrieve_hydrologic_soil_groups",
        lambda *args, **kwargs: calls.append(("hsg", kwargs)) or hsg,
    )
    monkeypatch.setattr(
        "ohqbuilder.input_downloader.retrieve_soil_texture",
        lambda *args, **kwargs: calls.append(("texture", kwargs)) or texture,
    )

    result = download_all_inputs(
        tmp_path, "SITE_A", lon=-111.2, lat=35.1, buffer_m=750, soil_top_depth=20
    )

    assert result.download_dir == phase1.download_dir
    assert [name for name, _ in calls] == ["phase1", "hsg", "texture"]
    assert calls[0][1]["products"] == "demlr,hydro,roads,landcover,atlas14"
    assert calls[1][1]["center"] == (-111.2, 35.1)
    assert calls[2][1]["top_depth"] == 20


def test_cli_download_inputs(monkeypatch, tmp_path, capsys):
    result = SimpleNamespace(
        download_dir=tmp_path / "downloads",
        hsg=SimpleNamespace(vector_path=tmp_path / "soils" / "hsg.gpkg"),
        texture=SimpleNamespace(vector_path=tmp_path / "soils" / "texture.gpkg"),
    )
    monkeypatch.setattr("ohqbuilder.cli.download_all_inputs", lambda *args, **kwargs: result)

    status = main(
        [
            "download-inputs",
            "--root",
            str(tmp_path),
            "--site",
            "SITE_A",
            "--lat",
            "35.1",
            "--lon",
            "-111.2",
        ]
    )

    assert status == 0
    assert "Downloaded DEM/hydrography" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lon": -181, "lat": 0}, "longitude"),
        ({"lon": 0, "lat": 91}, "latitude"),
        ({"lon": 0, "lat": 0, "buffer_m": 0}, "buffer_m"),
        ({"lon": 0, "lat": 0, "soil_pixel_size": 0}, "soil_pixel_size"),
        ({"lon": 0, "lat": 0, "soil_top_depth": 0}, "soil_top_depth"),
    ],
)
def test_download_all_inputs_validates_request_before_network(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        download_all_inputs(tmp_path, "SITE_A", **kwargs)

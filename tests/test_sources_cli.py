"""CLI tests for download-arcgis and download-socrata. No live network access."""

from __future__ import annotations

import pytest

from ohqbuilder.cli import build_parser, main
from ohqbuilder.sources.arcgis_rest import ArcGISRestError, BoundingBox
from ohqbuilder.sources.socrata import SocrataError


def test_download_arcgis_cli_forwards_names_and_defaults_manifest_path(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_download(service_url, output, **kwargs):
        calls.append((service_url, output, kwargs))
        return {"layers": [{"output_layer": "catch_basins_1"}]}

    monkeypatch.setattr("ohqbuilder.cli.download_arcgis_layers", fake_download)
    output = tmp_path / "storm.gpkg"
    status = main([
        "download-arcgis",
        "--service-url", "https://example.gov/arcgis/rest/services/Storm/FeatureServer",
        "--output", str(output),
        "--names", "Catch Basins",
    ])

    assert status == 0
    service_url, out, kwargs = calls[0]
    assert service_url == "https://example.gov/arcgis/rest/services/Storm/FeatureServer"
    assert out == str(output)
    assert kwargs["names"] == ["Catch Basins"]
    assert kwargs["pattern"] is None
    assert kwargs["bbox"] is None
    assert kwargs["manifest_path"] == str(output.with_name("storm.manifest.json"))
    assert "Wrote 1 layer(s)" in capsys.readouterr().out


def test_download_arcgis_cli_accepts_pattern_and_bbox(monkeypatch, tmp_path):
    calls = []

    def fake_download(service_url, output, **kwargs):
        calls.append(kwargs)
        return {"layers": []}

    monkeypatch.setattr("ohqbuilder.cli.download_arcgis_layers", fake_download)
    status = main([
        "download-arcgis",
        "--service-url", "https://example.gov/arcgis/rest/services/Storm/FeatureServer",
        "--output", str(tmp_path / "storm.gpkg"),
        "--pattern", "^Storm",
        "--bbox", "-118.5", "34.0", "-118.0", "34.5",
    ])

    assert status == 0
    kwargs = calls[0]
    assert kwargs["pattern"] == "^Storm"
    assert kwargs["names"] is None
    assert isinstance(kwargs["bbox"], BoundingBox)
    assert kwargs["bbox"].west == -118.5


def test_download_arcgis_cli_reports_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ArcGISRestError("no layer matched")

    monkeypatch.setattr("ohqbuilder.cli.download_arcgis_layers", fail)
    status = main([
        "download-arcgis",
        "--service-url", "https://example.gov/FeatureServer",
        "--output", str(tmp_path / "storm.gpkg"),
        "--names", "Nonexistent",
    ])
    assert status == 2


def test_download_arcgis_cli_rejects_both_names_and_pattern():
    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "download-arcgis", "--service-url", "https://example.gov/FeatureServer",
            "--output", "out.gpkg", "--names", "A", "--pattern", "B",
        ])


def test_download_socrata_cli_forwards_arguments(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_download(domain, dataset_id, output, **kwargs):
        calls.append((domain, dataset_id, output, kwargs))
        return {"layers": [{"output_layer": "inlets_ab12"}]}

    monkeypatch.setattr("ohqbuilder.cli.download_socrata_dataset", fake_download)
    output = tmp_path / "inlets.gpkg"
    status = main([
        "download-socrata",
        "--domain", "data.example-city.gov",
        "--dataset-id", "ab12-cd34",
        "--output", str(output),
        "--bbox", "-118.5", "34.0", "-118.0", "34.5",
        "--app-token", "secret-token",
    ])

    assert status == 0
    domain, dataset_id, out, kwargs = calls[0]
    assert domain == "data.example-city.gov"
    assert dataset_id == "ab12-cd34"
    assert out == str(output)
    assert isinstance(kwargs["bbox"], BoundingBox)
    assert kwargs["app_token"] == "secret-token"
    assert kwargs["manifest_path"] == str(output.with_name("inlets.manifest.json"))
    assert "Wrote 1 layer(s)" in capsys.readouterr().out


def test_download_socrata_cli_reports_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise SocrataError("no spatial column")

    monkeypatch.setattr("ohqbuilder.cli.download_socrata_dataset", fail)
    status = main([
        "download-socrata",
        "--domain", "data.example-city.gov",
        "--dataset-id", "ab12-cd34",
        "--output", str(tmp_path / "inlets.gpkg"),
    ])
    assert status == 2

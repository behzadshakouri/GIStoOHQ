import json

from ohqbuilder.cli import main
from ohqbuilder.dem_workflow import prepare_dem_from_config


def _feature(name, bounds, **props):
    minx, miny, maxx, maxy = bounds
    return {
        "type": "Feature",
        "properties": {"name": name, **props},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
        },
    }


def test_prepare_dem_from_config_writes_area_manifest_and_summary(tmp_path):
    tile_index = tmp_path / "indexes" / "tiles.geojson"
    tile_index.parent.mkdir()
    tile_index.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            _feature("inside", (-77.0, 39.0, -76.95, 39.05), path="dem/raw/tile_01.tif"),
            _feature("outside", (-75.0, 39.0, -74.95, 39.05), path="dem/raw/tile_02.tif"),
        ],
    }), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
outlet:
  longitude: -76.9765
  latitude: 38.9921
dem_acquisition:
  method: oriented_outlet_buffer
  acquisition_area: intermediate/dem_acquisition_area.geojson
  tile_index: indexes/tiles.geojson
  tile_manifest: intermediate/dem_download_manifest.json
  summary: intermediate/dem_workflow_summary.json
  upstream_km: 35
  downstream_km: 3
  lateral_km: 4
  azimuth: 20
""".strip(),
        encoding="utf-8",
    )

    result = prepare_dem_from_config(config)

    assert result.acquisition_area is not None
    assert result.tile_manifest is not None
    assert result.tile_manifest.selected_count == 1
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["selected_tile_count"] == 1
    assert (tmp_path / "intermediate" / "dem_acquisition_area.geojson").exists()
    assert (tmp_path / "intermediate" / "dem_download_manifest.json").exists()


def test_cli_prepare_dem_runs_config_workflow(tmp_path, capsys):
    tile_index = tmp_path / "tiles.geojson"
    tile_index.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("inside", (-77.0, 39.0, -76.95, 39.05), path="tile.tif")],
    }), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "outlet": {"longitude": -76.9765, "latitude": 38.9921},
        "dem_acquisition": {
            "method": "outlet_buffer",
            "acquisition_area": "area.geojson",
            "tile_index": "tiles.geojson",
            "tile_manifest": "manifest.json",
            "upstream_km": 10,
            "downstream_km": 1,
            "lateral_km": 2,
        },
    }), encoding="utf-8")

    assert main(["prepare-dem", "--config", str(config)]) == 0

    out = capsys.readouterr().out
    assert "Wrote DEM workflow summary:" in out
    assert "Selected tile count: 1" in out

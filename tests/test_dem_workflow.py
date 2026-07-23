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
    assert summary["raw_outlet"] == "inputs/outlet_raw.geojson"
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


def test_validate_dem_from_config_writes_summary_and_expansion(tmp_path):
    from ohqbuilder.dem_workflow import validate_dem_from_config

    acquisition = tmp_path / "area.geojson"
    watershed = tmp_path / "watershed.geojson"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("area", (-77.1, 38.9, -76.9, 39.1))],
    }), encoding="utf-8")
    watershed.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("watershed", (-77.099, 38.94, -76.95, 39.08))],
    }), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
dem_acquisition:
  acquisition_area: area.geojson
  watershed_boundary: watershed.geojson
  expanded_acquisition_area: expanded.geojson
  validation_summary: validation.json
  boundary_safety_distance_m: 500
  expansion_distance_km: 5
  auto_expand: true
""".strip(),
        encoding="utf-8",
    )

    result = validate_dem_from_config(config)

    assert not result.is_valid
    assert result.touched_edges == ("west",)
    assert result.expanded_area is not None
    summary = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert summary["expanded_acquisition_area"] == "expanded.geojson"


def test_cli_validate_dem_runs_config_workflow(tmp_path, capsys):
    acquisition = tmp_path / "area.geojson"
    watershed = tmp_path / "watershed.geojson"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("area", (-77.1, 38.9, -76.9, 39.1))],
    }), encoding="utf-8")
    watershed.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("watershed", (-77.0, 38.95, -76.95, 39.0))],
    }), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "dem_acquisition": {
            "acquisition_area": "area.geojson",
            "watershed_boundary": "watershed.geojson",
            "validation_summary": "validation.json",
            "boundary_safety_distance_m": 500,
        }
    }), encoding="utf-8")

    assert main(["validate-dem", "--config", str(config)]) == 0

    out = capsys.readouterr().out
    assert "Boundary validation: OK" in out
    assert (tmp_path / "validation.json").exists()


def test_prepare_dem_from_config_supports_upstream_network(tmp_path):
    flowlines = tmp_path / "hydro" / "flowlines.geojson"
    flowlines.parent.mkdir()
    flowlines.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "main"},
            "geometry": {
                "type": "LineString",
                "coordinates": [[-76.9765, 38.9921], [-76.99, 39.04], [-77.0, 39.08]],
            },
        }],
    }), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
outlet:
  longitude: -76.9765
  latitude: 38.9921
dem_acquisition:
  method: upstream_network
  flowline_path: hydro/flowlines.geojson
  acquisition_area: intermediate/dem_acquisition_area.geojson
  upstream_trace_distance_km: 15
  upstream_margin_km: 3
  downstream_margin_km: 1
  lateral_margin_km: 2
  envelope_type: oriented_rectangle
""".strip(),
        encoding="utf-8",
    )

    result = prepare_dem_from_config(config)

    assert result.acquisition_area is not None
    assert result.acquisition_area.mode == "upstream_network"
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["method"] == "upstream_network"
    assert (tmp_path / "intermediate" / "dem_acquisition_area.geojson").exists()


def test_write_dem_config_template_creates_upstream_network_config(tmp_path):
    from ohqbuilder.dem_workflow import write_dem_config_template

    config = write_dem_config_template(
        tmp_path / "SligoCreek.yaml",
        site="SligoCreek",
        lon=-76.9765,
        lat=38.9921,
        flowline_path="hydro/NHDFlowline.geojson",
        tile_index="indexes/usgs_3dep_tiles.geojson",
        target_crs="EPSG:26918",
    )

    text = config.read_text(encoding="utf-8")
    assert "method: upstream_network" in text
    assert "flowline_path: hydro/NHDFlowline.geojson" in text
    assert "tile_index: indexes/usgs_3dep_tiles.geojson" in text
    assert "target_crs: EPSG:26918" in text


def test_cli_init_dem_config_writes_next_step(tmp_path, capsys):
    config = tmp_path / "SligoCreek.json"

    assert main([
        "init-dem-config",
        "--config", str(config),
        "--site", "SligoCreek",
        "--lon", "-76.9765",
        "--lat", "38.9921",
        "--flowlines", "hydro/NHDFlowline.geojson",
        "--tile-index", "indexes/usgs_3dep_tiles.geojson",
        "--target-crs", "EPSG:26918",
    ]) == 0

    out = capsys.readouterr().out
    assert "Wrote DEM config:" in out
    assert "prepare-dem" in out
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["site"]["name"] == "SligoCreek"
    assert data["dem_acquisition"]["method"] == "upstream_network"


def test_infer_utm_crs_defaults_to_nad83_for_sligo_creek():
    from ohqbuilder.dem_workflow import infer_utm_crs

    assert infer_utm_crs(-76.9765, 38.9921) == "EPSG:26918"
    assert infer_utm_crs(-76.9765, 38.9921, datum="WGS84") == "EPSG:32618"


def test_write_dem_config_template_infers_target_crs(tmp_path):
    from ohqbuilder.dem_workflow import write_dem_config_template

    config = write_dem_config_template(
        tmp_path / "SligoCreek.json",
        site="SligoCreek",
        lon=-76.9765,
        lat=38.9921,
        flowline_path="hydro/NHDFlowline.geojson",
    )

    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["site"]["target_crs"] == "EPSG:26918"
    assert data["outlet"]["raw_path"] == "inputs/outlet_raw.geojson"


def test_prepare_dem_from_config_snaps_outlet_before_network_area(tmp_path):
    flowlines = tmp_path / "hydro" / "flowlines.geojson"
    flowlines.parent.mkdir()
    flowlines.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "LineString",
                "coordinates": [[-77.0, 39.0], [-76.9, 39.0]],
            },
        }],
    }), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
outlet:
  longitude: -76.95
  latitude: 39.001
  raw_path: inputs/outlet_raw.geojson
  snap_to_flowline: true
  snap_distance_m: 200
  snapped_path: inputs/outlet_snapped.geojson
dem_acquisition:
  method: upstream_network
  flowline_path: hydro/flowlines.geojson
  acquisition_area: intermediate/dem_acquisition_area.geojson
  upstream_trace_distance_km: 20
  upstream_margin_km: 2
  downstream_margin_km: 1
  lateral_margin_km: 1
""".strip(),
        encoding="utf-8",
    )

    result = prepare_dem_from_config(config)

    assert result.acquisition_area is not None
    raw = json.loads((tmp_path / "inputs" / "outlet_raw.geojson").read_text(encoding="utf-8"))
    assert raw["features"][0]["properties"]["source"] == "raw"
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["raw_outlet"] == "inputs/outlet_raw.geojson"
    assert summary["snapped_outlet"] == "inputs/outlet_snapped.geojson"
    assert summary["snap_distance_m"] < 200
    snapped = json.loads((tmp_path / "inputs" / "outlet_snapped.geojson").read_text(encoding="utf-8"))
    assert abs(snapped["features"][0]["geometry"]["coordinates"][1] - 39.0) < 0.00001

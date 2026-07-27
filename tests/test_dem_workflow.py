import json
from pathlib import Path

import pytest
import yaml

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


def test_prepare_dem_reports_invalid_yaml_without_traceback(tmp_path):
    from ohqbuilder.dem_workflow import DemWorkflowError, prepare_dem_from_config

    config = tmp_path / "config.yaml"
    config.write_text(
        "dem_acquisition:\n"
        "<<<<<<< Updated upstream\n"
        "  method: upstream_network\n",
        encoding="utf-8",
    )

    with pytest.raises(DemWorkflowError, match="Could not parse DEM workflow config"):
        prepare_dem_from_config(config)


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


def test_validate_dem_from_config_can_fallback_to_acquisition_area(tmp_path):
    from ohqbuilder.dem_workflow import validate_dem_from_config

    acquisition = tmp_path / "area.geojson"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("area", (-77.1, 38.9, -76.9, 39.1))],
    }), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
dem_acquisition:
  acquisition_area: area.geojson
  watershed_boundary: missing_watershed.geojson
  validation_summary: validation.json
  allow_acquisition_area_watershed_fallback: true
  auto_expand: false
""".strip(),
        encoding="utf-8",
    )

    result = validate_dem_from_config(config)

    assert not result.is_valid
    summary = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))
    assert summary["used_acquisition_area_watershed_fallback"] is True


def test_validate_dem_from_config_reports_missing_watershed(tmp_path):
    from ohqbuilder.dem_workflow import DemWorkflowError, validate_dem_from_config

    acquisition = tmp_path / "area.geojson"
    acquisition.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [_feature("area", (-77.1, 38.9, -76.9, 39.1))],
    }), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
dem_acquisition:
  acquisition_area: area.geojson
  watershed_boundary: missing_watershed.geojson
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(DemWorkflowError, match="Watershed boundary does not exist yet"):
        validate_dem_from_config(config)


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


def test_write_dem_config_template_defaults_sligo_demo_paths(tmp_path):
    from ohqbuilder.dem_workflow import write_dem_config_template

    demo_dir = tmp_path / "examples" / "SligoCreek"
    demo_dir.mkdir(parents=True)
    config = demo_dir / "dem_workflow.example.yaml"

    write_dem_config_template(
        config,
        site="SligoCreekDemo",
        lon=-76.99778601,
        lat=38.96888097,
        target_crs="EPSG:26918",
        method="upstream_network",
    )

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["dem_acquisition"]["flowline_path"] == "hydro/NHDFlowline.demo.geojson"
    assert data["dem_acquisition"]["tile_index"] == "indexes/usgs_3dep_tiles.demo.geojson"


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


def test_cli_run_dem_prep_runs_prepare_step(tmp_path, capsys):
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
  latitude: 39.0
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

    assert main(["run-dem-prep", "--config", str(config)]) == 0

    out = capsys.readouterr().out
    assert "Wrote DEM workflow summary:" in out
    assert "Wrote acquisition area:" in out
    assert (tmp_path / "intermediate" / "dem_acquisition_area.geojson").exists()


def test_cli_run_dem_prep_can_validate_existing_watershed(tmp_path, capsys):
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
        "outlet": {"longitude": -76.95, "latitude": 39.0},
        "dem_acquisition": {
            "method": "polygon",
            "acquisition_area": "area.geojson",
            "watershed_boundary": "watershed.geojson",
            "validation_summary": "validation.json",
            "boundary_safety_distance_m": 500,
        },
    }), encoding="utf-8")

    assert main(["run-dem-prep", "--config", str(config), "--validate"]) == 0

    out = capsys.readouterr().out
    assert "Boundary validation: OK" in out
    assert (tmp_path / "validation.json").exists()


def test_run_dem_prep_shell_wrapper_exists():
    script = Path("scripts/run_dem_prep.sh")

    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "ohqbuild run-dem-prep" in text
    assert "python -m ohqbuilder.cli run-dem-prep" in text


def test_sligo_creek_demo_config_runs_prepare(tmp_path):
    import shutil

    source = Path("examples/SligoCreek")
    project = tmp_path / "SligoCreek"
    shutil.copytree(source, project)

    assert main(["run-dem-prep", "--config", str(project / "dem_workflow.example.yaml")]) == 0

    assert (project / "inputs" / "outlet_raw.geojson").exists()
    assert (project / "inputs" / "outlet_snapped.geojson").exists()
    assert (project / "intermediate" / "dem_acquisition_area.geojson").exists()
    manifest = json.loads((project / "intermediate" / "dem_download_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tiles"] == ["dem/raw/demo_tile_sligo_01.tif"]

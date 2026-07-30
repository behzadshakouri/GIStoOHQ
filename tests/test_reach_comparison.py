import json

import pytest

from ohqbuilder.reach_comparison import ReachComparisonError, compare_reach_networks


def test_compare_reach_networks_reports_alignment_metrics(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    geometry = pytest.importorskip("shapely.geometry")
    generated = tmp_path / "reaches.gpkg"
    reference = tmp_path / "NHDFlowline_clip.gpkg"
    watershed = tmp_path / "watershed.gpkg"
    geopandas.GeoDataFrame(
        {"id": [1]}, geometry=[geometry.LineString([(0, 0), (100, 0)])], crs="EPSG:26918"
    ).to_file(generated, layer="reaches", driver="GPKG")
    geopandas.GeoDataFrame(
        {"id": [2]}, geometry=[geometry.LineString([(0, 10), (100, 10)])], crs="EPSG:26918"
    ).to_file(reference, layer="NHDFlowline_clip", driver="GPKG")
    geopandas.GeoDataFrame(
        {"id": [1]}, geometry=[geometry.box(-10, -10, 110, 20)], crs="EPSG:26918"
    ).to_file(watershed, layer="watershed", driver="GPKG")

    result = compare_reach_networks(
        generated, reference, tmp_path / "reach_comparison.json",
        watershed_path=watershed, tolerance_m=15.0,
    )
    payload = json.loads(result.read_text(encoding="utf-8"))

    assert payload["generated_length_km"] == pytest.approx(0.1)
    assert payload["reference_length_km"] == pytest.approx(0.1)
    assert payload["generated_within_tolerance_pct"] == pytest.approx(100.0)
    assert payload["reference_within_tolerance_pct"] == pytest.approx(100.0)
    assert payload["mean_lateral_offset_m"] == pytest.approx(10.0)
    assert payload["hausdorff_distance_m"] == pytest.approx(10.0)


def test_compare_reach_networks_rejects_nonpositive_tolerance(tmp_path):
    with pytest.raises(ReachComparisonError, match="must be positive"):
        compare_reach_networks("generated", "reference", tmp_path / "out.json", tolerance_m=0)

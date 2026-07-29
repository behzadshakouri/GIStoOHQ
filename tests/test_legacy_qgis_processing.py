from pathlib import Path


LEGACY_SCRIPTS = [
    Path("scripts/legacy_gis/fillsink_etc.py"),
    Path("scripts/legacy_gis/delineatewatershed.py"),
]


def test_legacy_qgis_scripts_do_not_require_processing_core_import():
    for script in LEGACY_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert "from processing.core.Processing import Processing" not in source
        assert "def initialize_processing" in source
        assert "Grass7AlgorithmProvider" in source
        assert "processing.algs.grass7.Grass7AlgorithmProvider" in source


def test_phase1_support_module_is_packaged():
    source = Path("scripts/legacy_gis/ws3io.py").read_text(encoding="utf-8")
    assert "def release_and_delete" in source
    assert "QgsProject" in source


def test_intermediate_topology_connectors_skip_invalid_zero_length_lines():
    materialize = Path("scripts/legacy_gis/materialize_junctions.py").read_text(
        encoding="utf-8"
    )
    derive = Path("scripts/legacy_gis/derive_topology_reaches.py").read_text(
        encoding="utf-8"
    )

    for source in (materialize, derive):
        assert "distance(end) < 0.01" in source or "distance(dst) < 0.01" in source
        assert "not geom.isGeosValid()" in source or "not geometry.isGeosValid()" in source


def test_legacy_grass_helpers_prefer_current_grass_prefix():
    for script in (
        Path("scripts/legacy_gis/delineate_whole_watershed.py"),
        Path("scripts/legacy_gis/extract_reaches.py"),
    ):
        source = script.read_text(encoding="utf-8")
        assert 'for prefix in ("grass:", "grass7:")' in source


def test_whole_watershed_has_python_water_outlet_fallback():
    source = Path("scripts/legacy_gis/delineate_whole_watershed.py").read_text(encoding="utf-8")
    assert "def delineate_watershed_with_flowdir" in source
    assert "GRASS r.water.outlet failed; using Python D8 fallback" in source


def test_legacy_scripts_register_native_provider():
    for script in (
        Path("scripts/legacy_gis/delineate_whole_watershed.py"),
        Path("scripts/legacy_gis/delineatewatershed.py"),
        Path("scripts/legacy_gis/extract_reaches.py"),
        Path("scripts/legacy_gis/fillsink_etc.py"),
    ):
        source = script.read_text(encoding="utf-8")
        assert "QgsNativeAlgorithms" in source
        assert 'providerById("native")' in source


def test_reach_extraction_adapts_threshold_to_tiny_demo_accumulation():
    source = Path("scripts/legacy_gis/extract_reaches.py").read_text(encoding="utf-8")

    assert "max_accumulation" in source
    assert "using adaptive threshold" in source
    assert "No raster-extracted reaches; clipping mapped flowlines as fallback" in source


def test_phase2_accepts_single_reach_watershed_without_interior_junctions():
    source = Path("scripts/legacy_gis/run_phase2.py").read_text(encoding="utf-8")

    junction_check = source.split("junctions_layer = validate_vector(", 1)[1].split(")", 1)[0]
    assert "minimum_features=0" in junction_check


def test_phase2_watershed_delineation_has_single_outlet_fallback_and_early_failure():
    source = Path("scripts/legacy_gis/delineatewatershed.py").read_text(encoding="utf-8")

    assert "def use_whole_watershed_fallback" in source
    assert 'os.path.join(OUT_DIR, "watershed_boundary.gpkg")' in source
    assert "Pour-point CRS transformed" in source
    assert "Pour point falls outside flow_acc.tif after CRS transformation" in source
    assert "No watershed polygons were generated" in source


def test_tc_converts_qvariant_attributes_before_arithmetic():
    source = Path("scripts/legacy_gis/compute_tc.py").read_text(encoding="utf-8")

    assert "def as_float(value):" in source
    assert 'Sp = as_float(ft["slope_pct"])' in source
    assert 'CN = as_float(ft["CN"])' in source


def test_cn_preparation_stamps_exact_dem_grid_and_rejects_empty_coverage():
    prep = Path("scripts/legacy_gis/prepcngrid.py").read_text(encoding="utf-8")
    build = Path("scripts/legacy_gis/buildcnraster.py").read_text(encoding="utf-8")

    assert "warped.SetGeoTransform(gt)" in prep
    assert "warped.SetProjection(srs)" in prep
    assert "Aligned CN inputs contain no classified cells" in prep
    assert "land cover and HSG geotransforms differ" in build
    assert "CN raster contains no classified cells" in build


def test_zonal_parameters_fail_early_when_cn_or_slope_has_no_coverage():
    cn = Path("scripts/legacy_gis/zonal_cn.py").read_text(encoding="utf-8")
    slope = Path("scripts/legacy_gis/extract_slope.py").read_text(encoding="utf-8")

    assert "CN is NULL for every subwatershed" in cn
    assert "Slope is NULL for every subwatershed" in slope
    assert 'slope_by_id[id_key(ft["id"])] = as_float' in slope


def test_phase_runners_suppress_only_qgsfield_deprecation_noise():
    for path in (
        Path("scripts/legacy_gis/run_phase1.py"),
        Path("scripts/legacy_gis/run_phase2.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert 'message="QgsField constructor is deprecated"' in source


def test_outlet_snap_warns_at_eighty_percent_of_search_radius():
    source = Path("scripts/legacy_gis/delineate_whole_watershed.py").read_text(
        encoding="utf-8"
    )

    assert 'globals().get("SNAP_EDGE_FRACTION", 0.80)' in source
    assert "SELECTED OUTLET IS FAR FROM THE ROUTED STREAM" in source
    assert "def outlet_snap_quality(distance_m):" in source
    assert 'return "GREEN"' in source
    assert 'return "YELLOW"' in source
    assert 'return "RED"' in source
    assert 'QgsField("quality", QVariant.String)' in source


def test_outlet_snap_prioritizes_cells_inside_maximum_accepted_move():
    source = Path("scripts/legacy_gis/delineate_whole_watershed.py").read_text(
        encoding="utf-8"
    )

    assert "accepted = valid & (distance <= MAX_OUTLET_SNAP_M)" in source
    assert "accepted_channel = accepted & (magnitude >= MIN_SNAP_ACC_CELLS)" in source
    assert "selection_mask = accepted_channel" in source
    assert "score[~selection_mask] = -np.inf" in source


def test_longest_flow_path_ranks_outlet_candidates_and_rejects_tiny_traversals():
    source = Path("scripts/legacy_gis/longestflowpath.py").read_text(encoding="utf-8")

    assert "def outlet_mask_candidates(" in source
    assert "np.abs(flow_acc[rows + row0, cols + col0])" in source
    assert "score = (reached, distance_m, -shift2)" in source
    assert "Refusing to write an implausibly short path" in source

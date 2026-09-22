from ohqbuilder.soil_pedotransfer import classify_usda_texture, van_genuchten_params


def test_classify_usda_texture_matches_known_reference_points():
    # Pure end-members and a few interior points, cross-checked against the
    # standard USDA-NRCS soil texture triangle.
    assert classify_usda_texture(sand_pct=95.0, clay_pct=3.0) == "sand"
    assert classify_usda_texture(sand_pct=25.0, clay_pct=5.0) == "silt_loam"
    assert classify_usda_texture(sand_pct=20.0, clay_pct=60.0) == "clay"
    assert classify_usda_texture(sand_pct=65.0, clay_pct=10.0) == "sandy_loam"
    assert classify_usda_texture(sand_pct=40.0, clay_pct=20.0) == "loam"


def test_van_genuchten_params_uses_carsel_parrish_1988_sand_values():
    # Sand: theta_r=0.045, theta_s=0.43, alpha=0.145 cm^-1, n=2.68, Ks=712.8 cm/day
    params = van_genuchten_params(sand_pct=95.0, clay_pct=3.0)
    assert params.theta_res == 0.045
    assert params.theta_sat == 0.43
    assert params.n_vG == 2.68
    assert abs(params.alpha_vG - 14.5) < 1e-9  # 0.145 cm^-1 -> 1/m
    assert abs(params.K_sat - 7.128) < 1e-9  # 712.8 cm/day -> m/day


def test_van_genuchten_params_uses_carsel_parrish_1988_clay_values():
    # Clay: theta_r=0.068, theta_s=0.38, alpha=0.008 cm^-1, n=1.09, Ks=4.8 cm/day
    params = van_genuchten_params(sand_pct=20.0, clay_pct=60.0)
    assert params.theta_res == 0.068
    assert params.theta_sat == 0.38
    assert params.n_vG == 1.09
    assert params.alpha_vG == 0.8
    assert abs(params.K_sat - 0.048) < 1e-9

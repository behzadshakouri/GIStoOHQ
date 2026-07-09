from ohqbuilder.usda import bbox_wkt, sanitize


def test_sanitize_and_bbox_wkt():
    assert sanitize("Site A/1") == "Site_A_1"
    assert bbox_wkt((0, 1, 2, 3), buffer=1) == "POLYGON ((-1 0, 3 0, 3 4, -1 4, -1 0))"

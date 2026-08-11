def test_import_readers():
    from ohqbuilder.readers.topology_reader import TopologyReader
    assert TopologyReader


def test_subbasin_reader_normalizes_gis_hru_properties():
    from ohqbuilder.readers.subbasin_reader import _impervious_fraction, _surface_elevation

    assert _impervious_fraction({"impervious_percent": 27.5}) == 0.275
    assert _impervious_fraction({"impervious_fraction": 0.31}) == 0.31
    assert _surface_elevation({"surface_elevation_m": 84.2}) == 84.2
    assert _surface_elevation({"mean_elevation_m": 72.8}) == 72.8

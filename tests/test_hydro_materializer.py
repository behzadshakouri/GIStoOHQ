from ohqbuilder.hydro_materializer import _flowline_vector_candidates, _preferred_hydro_archives


def test_preferred_hydro_archives_keeps_latest_vector_hu4(tmp_path):
    names = [
        "NHDPLUS_H_0206_HU4_20220324_RASTER.zip",
        "NHDPLUS_H_0206_HU4_20210101_GDB.zip",
        "NHDPLUS_H_0206_HU4_20240401_GDB.zip",
        "NHDPLUS_H_0206_HU4_20240501_RASTER.zip",
        "NHDPLUS_H_0206_HU4_20230301_SHAPE.zip",
        "NHDPLUS_H_02060001_HU8_20240401_GDB.zip",
    ]
    paths = []
    for index, name in enumerate(names, start=1):
        path = tmp_path / name
        path.write_bytes(b"x" * index)
        paths.append(path)

    assert _preferred_hydro_archives(paths) == [tmp_path / "NHDPLUS_H_0206_HU4_20230301_SHAPE.zip"]


def test_preferred_hydro_archives_falls_back_when_no_hu4(tmp_path):
    state = tmp_path / "NHD_State_20200101_GDB.zip"
    other = tmp_path / "NHD_Other_20210101_GDB.zip"
    state.write_bytes(b"xx")
    other.write_bytes(b"x")

    assert _preferred_hydro_archives([state, other]) == [other, state]


def test_flowline_vector_patterns_include_demo_geojson(tmp_path):
    geojson = tmp_path / "NHDFlowline.demo.geojson"
    geojson.write_text("{}", encoding="utf-8")
    notes = tmp_path / "flowline_notes.txt"
    notes.write_text("ignore", encoding="utf-8")

    assert _flowline_vector_candidates(tmp_path, tmp_path / "workspace") == [geojson]

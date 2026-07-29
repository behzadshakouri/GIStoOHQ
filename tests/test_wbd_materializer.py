import zipfile

import pytest

from ohqbuilder.wbd_materializer import (
    WbdMaterializeError,
    _safe_extract,
    materialize_wbd_reference,
)


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "wbd.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../escape.txt", "unsafe")

    with pytest.raises(WbdMaterializeError, match="Unsafe path"):
        _safe_extract(archive, tmp_path / "extract")

    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_accepts_normal_members(tmp_path):
    archive = tmp_path / "wbd.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("Shape/WBDHU12.dbf", "fixture")

    destination = tmp_path / "extract"
    _safe_extract(archive, destination)

    assert (destination / "Shape" / "WBDHU12.dbf").read_text() == "fixture"


def test_materialize_wbd_reference_selects_intersecting_huc12(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    shapely = pytest.importorskip("shapely.geometry")
    source_dir = tmp_path / "wbd"
    source_dir.mkdir()
    source = source_dir / "wbd.gpkg"
    geopandas.GeoDataFrame(
        {"huc12": ["020700100101", "020700100102"]},
        geometry=[
            shapely.box(-77.1, 38.9, -77.0, 39.0),
            shapely.box(-80.1, 35.0, -80.0, 35.1),
        ],
        crs="EPSG:4326",
    ).to_file(source, layer="WBDHU12", driver="GPKG")

    result = materialize_wbd_reference(
        source_dir,
        tmp_path / "outputs" / "WBDHU12_reference.gpkg",
        clip_bounds=(-77.2, 38.8, -76.9, 39.1),
    )

    selected = geopandas.read_file(result, layer="WBDHU12_reference")
    assert selected["huc12"].tolist() == ["020700100101"]

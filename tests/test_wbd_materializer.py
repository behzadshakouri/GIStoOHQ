import zipfile

import pytest

from ohqbuilder.wbd_materializer import (
    WbdMaterializeError,
    _find_hu12_layer,
    _safe_extract,
    _service_hu12_layer,
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


def test_find_hu12_layer_accepts_provider_name_variants():
    assert _find_hu12_layer(["WBD_HU12_1"]) == "WBD_HU12_1"
    assert _find_hu12_layer(["metadata", "WBD/WBDHU12"]) == "WBD/WBDHU12"
    assert _find_hu12_layer(["12-digit HU (Subwatershed)"]) == "12-digit HU (Subwatershed)"
    assert _find_hu12_layer(["WBDHU10"]) is None


def test_service_hu12_layer_discovers_layer_id_from_name(monkeypatch):
    monkeypatch.setattr(
        "ohqbuilder.wbd_materializer._read_json",
        lambda *args, **kwargs: {
            "layers": [
                {"id": 4, "name": "WBDHU10"},
                {"id": 6, "name": "WBD_HU12"},
            ]
        },
    )

    assert _service_hu12_layer("https://example.test/MapServer", timeout=1.0) == 6


def test_service_hu12_layer_reports_available_layers(monkeypatch):
    monkeypatch.setattr(
        "ohqbuilder.wbd_materializer._read_json",
        lambda *args, **kwargs: {"layers": [{"id": 4, "name": "WBDHU10"}]},
    )

    with pytest.raises(WbdMaterializeError, match="Available layers: WBDHU10"):
        _service_hu12_layer("https://example.test/MapServer", timeout=1.0)


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


def test_raster_only_archive_is_rejected_without_extraction(tmp_path, monkeypatch):
    source = tmp_path / "wbd"
    source.mkdir()
    with zipfile.ZipFile(source / "NHD_RASTER.zip", "w") as archive:
        archive.writestr("elevation/dem.tif", b"not a vector dataset")
    def unexpected_extraction(*args):
        pytest.fail("Raster-only archive must not be extracted")
    monkeypatch.setattr("ohqbuilder.wbd_materializer._safe_extract", unexpected_extraction)
    with pytest.raises(WbdMaterializeError, match="No WBD vector package"):
        materialize_wbd_reference(source, tmp_path / "result.gpkg", clip_bounds=(-77, 38, -76, 39))


def test_huc12_selection_searches_later_regional_archives(tmp_path):
    gpd = pytest.importorskip("geopandas")
    geometry = pytest.importorskip("shapely.geometry")
    source = tmp_path / "downloads"
    source.mkdir()
    for index, bounds in enumerate(((-80, 35, -79, 36), (-77.1, 38.9, -77, 39))):
        folder = tmp_path / f"region{index}"
        folder.mkdir()
        shp = folder / "WBDHU12.shp"
        gpd.GeoDataFrame({"huc12": [str(index)]}, geometry=[geometry.box(*bounds)],
                         crs="EPSG:4326").to_file(shp)
        with zipfile.ZipFile(source / f"region{index}.zip", "w") as archive:
            for item in folder.iterdir():
                archive.write(item, item.name)
            archive.writestr("unused/dem.tif", b"unrelated raster")
    result = materialize_wbd_reference(source, tmp_path / "selected.gpkg",
                                       clip_bounds=(-77.2, 38.8, -76.9, 39.1))
    assert gpd.read_file(result)["huc12"].tolist() == ["1"]


def test_raster_provenance_containers_do_not_trigger_extraction(tmp_path, monkeypatch):
    source = tmp_path / "wbd"
    source.mkdir()
    with zipfile.ZipFile(source / "NHD_RASTER.zip", "w") as archive:
        archive.writestr("raster/elev_source.gdb/a00001.gdbtable", b"provenance")
        archive.writestr("raster/elev_source.gpkg", b"provenance")
        archive.writestr("raster/dem.tif", b"raster")
    monkeypatch.setattr("ohqbuilder.wbd_materializer._safe_extract",
                        lambda *a: pytest.fail("No WBD dataset to extract"))
    with pytest.raises(WbdMaterializeError, match="No WBD vector package"):
        materialize_wbd_reference(source, tmp_path / "selected.gpkg",
                                  clip_bounds=(-77.2, 38.8, -76.9, 39.1))

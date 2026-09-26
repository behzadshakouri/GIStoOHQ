import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin

from ohqbuilder.landcover_crosswalk import map_landcover, read_crosswalk


def _raster(path, values):
    with rasterio.open(path, "w", driver="GTiff", width=values.shape[1], height=values.shape[0],
                       count=1, dtype="uint8", nodata=0, crs="EPSG:4326",
                       transform=from_origin(44, 37, 0.001, 0.001)) as target:
        target.write(values, 1)


def _mapping(path):
    path.write_text(json.dumps({
        "source_dataset": "Example global land cover", "source_year": 2021,
        "source_url": "https://example.org/product", "method": "Reviewed site-specific class mapping",
        "classes": {"10": 41, "50": 24},
    }))


def test_mapped_landcover_preserves_nodata_and_provenance(tmp_path):
    source, crosswalk, output = (tmp_path / name for name in ("source.tif", "map.json", "mapped.tif"))
    _raster(source, np.array([[10, 50], [0, 10]], dtype="uint8"))
    _mapping(crosswalk)
    map_landcover(source, crosswalk, output)
    with rasterio.open(output) as raster:
        assert raster.read(1).tolist() == [[41, 24], [255, 41]]
        assert raster.nodata == 255
    provenance = json.loads(Path(str(output) + ".provenance.json").read_text())
    assert provenance["source_pixel_counts"] == {"10": 2, "50": 1}
    assert provenance["source_dataset"] == "Example global land cover"


def test_unknown_source_class_rejected_without_publishing(tmp_path):
    source, crosswalk, output = (tmp_path / name for name in ("source.tif", "map.json", "mapped.tif"))
    _raster(source, np.array([[10, 60]], dtype="uint8"))
    _mapping(crosswalk)
    with pytest.raises(ValueError, match="Unmapped land-cover class"):
        map_landcover(source, crosswalk, output)
    assert not output.exists()


def test_target_must_be_in_cn_lookup(tmp_path):
    crosswalk = tmp_path / "map.json"
    _mapping(crosswalk)
    data = json.loads(crosswalk.read_text())
    data["classes"]["10"] = 50
    crosswalk.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="absent from cn_lookup"):
        read_crosswalk(crosswalk)

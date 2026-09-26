import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")
rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin

from ohqbuilder.soilgrids_wcs import coverage_url, download_texture


def test_request_uses_native_crs_and_bounds():
    url = coverage_url("sand", "0-5cm", (-1700000, 1500000, -1698000, 1502000))
    assert "sand_0-5cm_Q0.5" in url
    assert "152160" in url
    assert "SUBSET=X%28-1700000%2C-1698000%29" in url
    with pytest.raises(ValueError, match="300 km"):
        coverage_url("sand", "0-5cm", (0, 0, 400000, 1000))


def test_download_records_three_raw_predictions_without_hsg(tmp_path):
    raster_path = tmp_path / "source.tif"
    with rasterio.open(raster_path, "w", driver="GTiff", width=2, height=2,
                       count=1, dtype="int16", nodata=-32768, crs="EPSG:4326",
                       transform=from_origin(44, 37, 0.01, 0.01)) as raster:
        raster.write(np.full((2, 2), 450, dtype="int16"), 1)
    data = raster_path.read_bytes()
    with TemporaryDirectory() as directory:
        with patch("ohqbuilder.soilgrids_wcs.urllib.request.urlopen", side_effect=lambda *a, **k: BytesIO(data)):
            manifest = download_texture((-1700000, 1500000, -1698000, 1502000), "0-5cm", directory)
        contents = json.loads(manifest.read_text())
        assert set(contents["properties"]) == {"sand", "silt", "clay"}
        assert contents["units"] == "g/kg"
        assert "not derived" in contents["hsg_status"]
        assert not (Path(directory) / "hsg.tif").exists()

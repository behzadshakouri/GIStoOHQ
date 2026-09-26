import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ohqbuilder.copernicus_dem import _validate_raster, download_copernicus_dem, tile_names


class CopernicusDemTests(unittest.TestCase):
    def test_tile_boundaries_and_limits(self):
        self.assertEqual(tile_names((44, 36, 45, 37)), ["Copernicus_DSM_COG_10_N36_00_E044_00_DEM"])
        self.assertEqual(len(tile_names((-77.2, 38.9, -76.9, 39.1))), 4)
        with self.assertRaises(ValueError):
            tile_names((-77.2, 38.9, -76.9, 39.1), max_tiles=3)

    def test_index_missing_tile_fails_before_writing(self):
        with TemporaryDirectory() as directory:
            with patch("ohqbuilder.copernicus_dem.urllib.request.urlopen", return_value=BytesIO(b"other tile\r\n")):
                with self.assertRaisesRegex(ValueError, "does not list tile"):
                    download_copernicus_dem((44, 36, 45, 37), directory)
            self.assertFalse(list(Path(directory).iterdir()))

    def test_download_records_verified_source_and_reuses_tile(self):
        name = tile_names((44, 36, 45, 37))[0]
        calls = []

        def open_url(url, **_):
            calls.append(url)
            return BytesIO((name + "\r\n").encode() if url.endswith("tileList.txt") else b"raster bytes")

        with TemporaryDirectory() as directory:
            with patch("ohqbuilder.copernicus_dem.urllib.request.urlopen", side_effect=open_url), patch("ohqbuilder.copernicus_dem._validate_raster"):
                manifest = download_copernicus_dem((44, 36, 45, 37), directory)
                first = json.loads(manifest.read_text())
                self.assertEqual(first["tiles"][0]["path"], name + ".tif")
                self.assertEqual((Path(directory) / (name + ".tif")).read_bytes(), b"raster bytes")
                download_copernicus_dem((44, 36, 45, 37), directory)
            self.assertEqual(len([url for url in calls if url.endswith(".tif")]), 1)

    def test_raster_validation_detects_corruption_and_wrong_extent(self):
        try:
            import numpy as np
            import rasterio
            from rasterio.transform import from_bounds
        except ImportError:
            self.skipTest("GIS dependencies not installed")
        name = tile_names((44, 36, 45, 37))[0]
        with TemporaryDirectory() as directory:
            target = Path(directory) / (name + ".tif")
            with rasterio.open(target, "w", driver="GTiff", width=2, height=2,
                               count=1, dtype="float32", crs="EPSG:4326",
                               transform=from_bounds(44, 36, 45, 37, 2, 2)) as dataset:
                dataset.write(np.ones((2, 2), dtype="float32"), 1)
            _validate_raster(target)
            target.write_bytes(b"not a TIFF")
            with self.assertRaises(Exception):
                _validate_raster(target)


if __name__ == "__main__":
    unittest.main()

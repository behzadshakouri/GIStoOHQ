import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ohqbuilder.osm_vectors import download_osm_vectors, overpass_geojson, overpass_query


class OsmVectorTests(unittest.TestCase):
    def test_bounded_query_and_rejects_unbounded_request(self):
        query = overpass_query((44.0, 36.0, 44.2, 36.1), "hydro")
        self.assertIn('(36.0,44.0,36.1,44.2)', query)
        self.assertIn('river|stream', query)
        with self.assertRaises(ValueError):
            overpass_query((-180, -90, 180, 90), "roads")

    def test_only_complete_waterways_survive(self):
        data = {"elements": [
            {"type": "way", "id": 7, "tags": {"waterway": "river", "name": "Example"},
             "geometry": [{"lat": 36, "lon": 44}, {"lat": 36.1, "lon": 44.1}]},
            {"type": "way", "id": 8, "tags": {"highway": "primary"},
             "geometry": [{"lat": 36, "lon": 44}, {"lat": 36.1, "lon": 44.1}]},
            {"type": "way", "id": 9, "tags": {"waterway": "stream"},
             "geometry": [{"lat": 36, "lon": 44}]},
        ]}
        features = overpass_geojson(data, "hydro")["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["osm_id"], 7)
        self.assertEqual(features[0]["geometry"]["coordinates"][0], [44, 36])

    def test_download_writes_reviewable_lines_and_keeps_existing_file_on_bad_response(self):
        valid = b'{"elements":[{"type":"way","id":1,"tags":{"waterway":"river"},"geometry":[{"lon":44,"lat":36},{"lon":44.1,"lat":36.1}]}]}'
        with TemporaryDirectory() as directory:
            target = Path(directory) / "OSMFlowline.geojson"
            with patch("ohqbuilder.osm_vectors.urllib.request.urlopen", return_value=BytesIO(valid)):
                download_osm_vectors((44, 36, 44.2, 36.2), "hydro", target)
            saved = target.read_bytes()
            self.assertIn(b'"osm_id": 1', saved)
            with patch("ohqbuilder.osm_vectors.urllib.request.urlopen", return_value=BytesIO(b'{"elements":[]}')):
                with self.assertRaises(ValueError):
                    download_osm_vectors((44, 36, 44.2, 36.2), "hydro", target)
            self.assertEqual(target.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()

"""Tests for the generic Socrata geospatial downloader. No live network access."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from ohqbuilder.sources.arcgis_rest import BoundingBox
from ohqbuilder.sources.socrata import (
    SocrataError,
    download_dataset_features,
    download_socrata_dataset,
    fetch_dataset_metadata,
    geo_column_name,
    write_features_to_gpkg,
)

gpd = pytest.importorskip("geopandas")

DOMAIN = "data.example-city.gov"
DATASET_ID = "ab12-cd34"

METADATA = {
    "id": DATASET_ID,
    "name": "Storm Drain Inlets",
    "columns": [
        {"fieldName": "the_geom", "name": "the_geom", "dataTypeName": "point"},
        {"fieldName": "asset_id", "name": "Asset ID", "dataTypeName": "text"},
        {"fieldName": "condition", "name": "Condition", "dataTypeName": "text"},
    ],
}


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


def _json_response(payload) -> _Response:
    return _Response(json.dumps(payload).encode("utf-8"))


def _url_of(resource) -> str:
    return resource if isinstance(resource, str) else resource.full_url


def _inlet_feature(object_id, asset_id, condition, lon, lat):
    return {
        "type": "Feature",
        "properties": {"asset_id": asset_id, "condition": condition},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "id": str(object_id),
    }


class FakeSocrataPortal:
    """A minimal in-memory stand-in for a Socrata domain."""

    def __init__(self, metadata, pages):
        self.metadata = metadata
        self.pages = list(pages)
        self.calls: list[str] = []

    def opener(self, resource, timeout):
        url = _url_of(resource)
        self.calls.append(url)
        path = url.split("?", 1)[0]
        if path.endswith(".json"):
            return _json_response(self.metadata)
        if not self.pages:
            return _json_response({"type": "FeatureCollection", "features": []})
        return _json_response(self.pages.pop(0))


# --- metadata before download --------------------------------------------------

def test_metadata_is_fetched_before_any_feature_page():
    portal = FakeSocrataPortal(METADATA, [{"type": "FeatureCollection", "features": []}])
    fetch_dataset_metadata(DOMAIN, DATASET_ID, opener=portal.opener)
    assert len(portal.calls) == 1
    assert portal.calls[0].split("?")[0].endswith(f"/api/views/{DATASET_ID}.json")


def test_geo_column_name_finds_the_first_spatial_column():
    assert geo_column_name(METADATA) == "the_geom"
    assert geo_column_name({"columns": [{"fieldName": "x", "dataTypeName": "text"}]}) is None


# --- pagination ---------------------------------------------------------------

def test_download_dataset_features_pages_with_limit_and_offset():
    pages = [
        {"type": "FeatureCollection", "features": [
            _inlet_feature(1, "SD-1", "Good", -118.1, 34.1),
            _inlet_feature(2, "SD-2", None, -118.2, 34.2),
        ]},
        {"type": "FeatureCollection", "features": [
            _inlet_feature(3, "SD-3", "Poor", -118.3, 34.3),
        ]},
    ]
    portal = FakeSocrataPortal(METADATA, pages)
    features, _info = download_dataset_features(
        DOMAIN, DATASET_ID, page_size=2, opener=portal.opener
    )
    assert len(features) == 3
    assert features[1]["properties"]["condition"] is None  # null preserved
    data_calls = [c for c in portal.calls if "/resource/" in c]
    assert len(data_calls) == 2
    first_params = urllib.parse.parse_qs(data_calls[0].split("?", 1)[1])
    second_params = urllib.parse.parse_qs(data_calls[1].split("?", 1)[1])
    assert first_params["$limit"] == ["2"]
    assert first_params["$offset"] == ["0"]
    assert second_params["$offset"] == ["2"]
    assert first_params["$order"] == [":id"]  # stable default ordering


def test_download_dataset_features_detects_a_server_that_does_not_advance():
    stuck_page = {"type": "FeatureCollection", "features": [
        _inlet_feature(1, "SD-1", "Good", -118.1, 34.1),
        _inlet_feature(2, "SD-2", "Good", -118.2, 34.2),
    ]}
    portal = FakeSocrataPortal(METADATA, [stuck_page, stuck_page])
    with pytest.raises(SocrataError, match="duplicate page"):
        download_dataset_features(DOMAIN, DATASET_ID, page_size=2, opener=portal.opener)


# --- bounding-box filtering -----------------------------------------------------

def test_download_dataset_features_applies_bbox_as_within_box_where_clause():
    portal = FakeSocrataPortal(METADATA, [{"type": "FeatureCollection", "features": []}])
    bbox = BoundingBox(west=-118.5, south=34.0, east=-118.0, north=34.5)
    download_dataset_features(DOMAIN, DATASET_ID, bbox=bbox, opener=portal.opener)
    data_call = next(c for c in portal.calls if "/resource/" in c)
    params = urllib.parse.parse_qs(data_call.split("?", 1)[1])
    assert "within_box(the_geom, 34.5, -118.5, 34.0, -118.0)" in params["$where"][0]


def test_bbox_without_a_spatial_column_fails_loudly_instead_of_ignoring_the_filter():
    no_geo_metadata = {"columns": [{"fieldName": "asset_id", "dataTypeName": "text"}]}
    portal = FakeSocrataPortal(no_geo_metadata, [])
    bbox = BoundingBox(west=-118.5, south=34.0, east=-118.0, north=34.5)
    with pytest.raises(SocrataError, match="no spatial column"):
        download_dataset_features(DOMAIN, DATASET_ID, metadata=no_geo_metadata, bbox=bbox, opener=portal.opener)


# --- empty result ---------------------------------------------------------------

def test_write_features_to_gpkg_writes_an_empty_layer_with_metadata_schema(tmp_path):
    output = tmp_path / "out.gpkg"
    write_features_to_gpkg([], output, "storm_drain_inlets", metadata=METADATA)
    frame = gpd.read_file(output, layer="storm_drain_inlets")
    assert len(frame) == 0
    assert set(frame.columns) >= {"the_geom", "asset_id", "condition"}


# --- manifest --------------------------------------------------------------------

def test_download_socrata_dataset_writes_gpkg_and_manifest(tmp_path):
    pages = [{"type": "FeatureCollection", "features": [
        _inlet_feature(1, "SD-1", "Good", -118.1, 34.1),
        _inlet_feature(2, "SD-2", None, -118.2, 34.2),
    ]}]
    portal = FakeSocrataPortal(METADATA, pages)
    output = tmp_path / "inlets.gpkg"
    manifest_path = tmp_path / "inlets.manifest.json"

    manifest = download_socrata_dataset(
        DOMAIN, DATASET_ID, output, manifest_path=manifest_path, opener=portal.opener,
    )

    assert manifest["provider"] == "socrata"
    assert manifest["source"] == f"{DOMAIN}/{DATASET_ID}"
    assert len(manifest["layers"]) == 1
    record = manifest["layers"][0]
    assert record["feature_count"] == 2
    assert record["crs"] == "EPSG:4326"
    assert record["layer_metadata"]["dataset_id"] == DATASET_ID
    assert record["layer_metadata"]["geo_column"] == "the_geom"
    assert manifest["downloaded_at"]

    frame = gpd.read_file(output, layer=record["output_layer"])
    assert len(frame) == 2

    on_disk = json.loads(manifest_path.read_text())
    assert on_disk == manifest

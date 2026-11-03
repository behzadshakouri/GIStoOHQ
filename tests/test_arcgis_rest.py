"""Tests for the generic ArcGIS REST downloader. No live network access."""

from __future__ import annotations

import http.client
import json
import urllib.parse

import pytest

from ohqbuilder.sources.arcgis_rest import (
    ArcGISRestError,
    BoundingBox,
    discover_layers,
    download_arcgis_layers,
    download_layer_features,
    select_layers,
    write_features_to_gpkg,
)

gpd = pytest.importorskip("geopandas")

SERVICE_URL = "https://example-city.gov/arcgis/rest/services/Stormwater/FeatureServer"


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


class FakeArcGISService:
    """A minimal in-memory stand-in for an ArcGIS Server feature service."""

    def __init__(self, discovery_payload, layer_metadata, layer_pages=None):
        self.discovery_payload = discovery_payload
        self.layer_metadata = layer_metadata
        self.layer_pages = {k: list(v) for k, v in (layer_pages or {}).items()}
        self.calls: list[str] = []

    def opener(self, resource, timeout):
        url = _url_of(resource)
        self.calls.append(url)
        path = url.split("?", 1)[0]
        if path == SERVICE_URL:
            return _json_response(self.discovery_payload)
        if path.endswith("/query"):
            layer_id = int(path[len(SERVICE_URL) + 1:-len("/query")])
            queue = self.layer_pages.get(layer_id, [])
            if not queue:
                return _json_response({"type": "FeatureCollection", "features": []})
            return _json_response(queue.pop(0))
        layer_id = int(path[len(SERVICE_URL) + 1:])
        return _json_response(self.layer_metadata[layer_id])


NESTED_DISCOVERY = {
    "layers": [
        {"id": 0, "name": "Storm Drainage", "type": "Group Layer",
         "parentLayerId": -1, "subLayerIds": [1, 2]},
        {"id": 1, "name": "Catch Basins", "type": "Feature Layer",
         "parentLayerId": 0, "geometryType": "esriGeometryPoint", "subLayerIds": None},
        {"id": 2, "name": "Storm Pipes", "type": "Feature Layer",
         "parentLayerId": 0, "geometryType": "esriGeometryPolyline", "subLayerIds": None},
        {"id": 3, "name": "Parcels", "type": "Feature Layer",
         "parentLayerId": -1, "geometryType": "esriGeometryPolygon", "subLayerIds": None},
    ],
    "tables": [
        {"id": 4, "name": "Inspection Log"},
    ],
}

CATCH_BASIN_METADATA = {
    "name": "Catch Basins", "type": "Feature Layer", "geometryType": "esriGeometryPoint",
    "objectIdField": "OBJECTID", "maxRecordCount": 2,
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID"},
        {"name": "ASSETID", "type": "esriFieldTypeString"},
        {"name": "CONDITION", "type": "esriFieldTypeString"},
    ],
}


def _catch_basin_feature(oid, asset_id, condition, lon, lat):
    return {
        "type": "Feature",
        "properties": {"OBJECTID": oid, "ASSETID": asset_id, "CONDITION": condition},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def _crs(epsg=4326):
    return {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}


# --- discovery, including nested group layers -------------------------------

def test_discover_layers_includes_nested_group_hierarchy():
    service = FakeArcGISService(NESTED_DISCOVERY, {})
    layers = discover_layers(SERVICE_URL, opener=service.opener)
    by_id = {layer.layer_id: layer for layer in layers}
    assert by_id[0].layer_type == "Group Layer"
    assert by_id[0].sub_layer_ids == (1, 2)
    assert by_id[0].parent_layer_id is None  # Esri's -1 sentinel normalized to None
    assert by_id[1].parent_layer_id == 0
    assert by_id[1].is_feature_layer is True
    assert by_id[0].is_feature_layer is False
    assert by_id[1].geometry_type == "esriGeometryPoint"
    assert by_id[4].layer_type == "Table"  # from the "tables" collection


def test_discover_layers_rejects_a_response_with_no_layers_or_tables():
    service = FakeArcGISService({"layers": [], "tables": []}, {})
    with pytest.raises(ArcGISRestError, match="no layers or tables"):
        discover_layers(SERVICE_URL, opener=service.opener)


# --- name-based selection, no hardcoded IDs ----------------------------------

def test_select_layers_by_exact_name_without_hardcoded_ids():
    service = FakeArcGISService(NESTED_DISCOVERY, {})
    layers = discover_layers(SERVICE_URL, opener=service.opener)
    selected = select_layers(layers, names=["Catch Basins"])
    assert [layer.layer_id for layer in selected] == [1]


def test_select_layers_by_name_raises_when_missing_instead_of_silently_skipping():
    service = FakeArcGISService(NESTED_DISCOVERY, {})
    layers = discover_layers(SERVICE_URL, opener=service.opener)
    with pytest.raises(ArcGISRestError, match="No layer matched"):
        select_layers(layers, names=["Does Not Exist"])


def test_select_layers_by_regex_excludes_group_layers_by_default():
    service = FakeArcGISService(NESTED_DISCOVERY, {})
    layers = discover_layers(SERVICE_URL, opener=service.opener)
    # "Storm Drainage" (the group layer) and "Storm Pipes" both match "^Storm",
    # but feature_layers_only=True must exclude the group layer.
    selected = select_layers(layers, pattern="^Storm")
    assert [layer.layer_id for layer in selected] == [2]


def test_select_layers_rejects_both_names_and_pattern():
    service = FakeArcGISService(NESTED_DISCOVERY, {})
    layers = discover_layers(SERVICE_URL, opener=service.opener)
    with pytest.raises(ArcGISRestError, match="exactly one"):
        select_layers(layers, names=["Catch Basins"], pattern="^Storm")


# --- multi-page downloads, stable ordering, max record count ----------------

def test_download_layer_features_pages_through_all_records_in_oid_order():
    pages = [
        {"type": "FeatureCollection", "crs": _crs(), "features": [
            _catch_basin_feature(1, "CB-1", "Good", -118.1, 34.1),
            _catch_basin_feature(2, "CB-2", None, -118.2, 34.2),
        ]},
        {"type": "FeatureCollection", "features": [
            _catch_basin_feature(3, "CB-3", "Poor", -118.3, 34.3),
        ]},
    ]
    service = FakeArcGISService(NESTED_DISCOVERY, {1: CATCH_BASIN_METADATA}, {1: pages})
    features, info = download_layer_features(SERVICE_URL, 1, opener=service.opener)
    assert [f["properties"]["OBJECTID"] for f in features] == [1, 2, 3]
    assert info["object_id_field"] == "OBJECTID"
    assert info["server_max_record_count"] == 2
    assert info["effective_page_size"] == 2
    # Two query calls were made (one per page), each honoring maxRecordCount=2.
    query_calls = [c for c in service.calls if c.split("?")[0].endswith("/query")]
    assert len(query_calls) == 2
    for call in query_calls:
        params = urllib.parse.parse_qs(call.split("?", 1)[1])
        assert params["resultRecordCount"] == ["2"]
        assert params["orderByFields"] == ["OBJECTID ASC"]


def test_download_layer_features_preserves_null_attributes():
    pages = [{"type": "FeatureCollection", "features": [
        _catch_basin_feature(1, "CB-1", "Good", -118.1, 34.1),
        _catch_basin_feature(2, "CB-2", None, -118.2, 34.2),
    ]}]
    service = FakeArcGISService(NESTED_DISCOVERY, {1: CATCH_BASIN_METADATA}, {1: pages})
    features, _ = download_layer_features(SERVICE_URL, 1, opener=service.opener)
    assert features[1]["properties"]["CONDITION"] is None


def test_download_layer_features_honors_requested_page_size_under_server_max():
    metadata = dict(CATCH_BASIN_METADATA, maxRecordCount=1000)
    pages = [
        {"type": "FeatureCollection", "features": [_catch_basin_feature(1, "CB-1", "Good", -118.1, 34.1)]},
        {"type": "FeatureCollection", "features": []},
    ]
    service = FakeArcGISService(NESTED_DISCOVERY, {1: metadata}, {1: pages})
    _, info = download_layer_features(SERVICE_URL, 1, page_size=1, opener=service.opener)
    assert info["effective_page_size"] == 1  # min(requested, server max)


# --- bounding-box clipping ----------------------------------------------------

def test_download_layer_features_applies_bbox_parameters():
    pages = [{"type": "FeatureCollection", "features": [
        _catch_basin_feature(1, "CB-1", "Good", -118.1, 34.1),
    ]}]
    service = FakeArcGISService(NESTED_DISCOVERY, {1: CATCH_BASIN_METADATA}, {1: pages})
    bbox = BoundingBox(west=-118.5, south=34.0, east=-118.0, north=34.5)
    download_layer_features(SERVICE_URL, 1, bbox=bbox, opener=service.opener)
    query_call = next(c for c in service.calls if c.split("?")[0].endswith("/query"))
    params = urllib.parse.parse_qs(query_call.split("?", 1)[1])
    assert params["geometry"] == ["-118.5,34.0,-118.0,34.5"]
    assert params["geometryType"] == ["esriGeometryEnvelope"]
    assert params["spatialRel"] == ["esriSpatialRelIntersects"]
    assert params["inSR"] == ["4326"]


def test_bounding_box_rejects_inverted_bounds():
    with pytest.raises(ArcGISRestError, match="west < east"):
        BoundingBox(west=10, south=0, east=5, north=1)


# --- duplicate-page protection ------------------------------------------------

def test_download_layer_features_detects_a_server_that_does_not_advance():
    stuck_page = {"type": "FeatureCollection", "features": [
        _catch_basin_feature(1, "CB-1", "Good", -118.1, 34.1),
        _catch_basin_feature(2, "CB-2", "Good", -118.2, 34.2),
    ]}
    # The server returns the exact same two records again at offset=2 instead
    # of advancing -- this must raise, not silently duplicate the rows.
    service = FakeArcGISService(
        NESTED_DISCOVERY, {1: CATCH_BASIN_METADATA}, {1: [stuck_page, stuck_page]}
    )
    with pytest.raises(ArcGISRestError, match="duplicate page"):
        download_layer_features(SERVICE_URL, 1, opener=service.opener)


# --- empty layers --------------------------------------------------------------

def test_download_layer_features_handles_a_layer_with_zero_matching_records():
    service = FakeArcGISService(NESTED_DISCOVERY, {1: CATCH_BASIN_METADATA}, {1: []})
    features, info = download_layer_features(SERVICE_URL, 1, opener=service.opener)
    assert features == []
    assert info["object_id_field"] == "OBJECTID"


def test_write_features_to_gpkg_writes_an_empty_layer_with_metadata_schema(tmp_path):
    output = tmp_path / "out.gpkg"
    write_features_to_gpkg([], output, "catch_basins", metadata=CATCH_BASIN_METADATA)
    frame = gpd.read_file(output, layer="catch_basins")
    assert len(frame) == 0
    assert set(frame.columns) >= {"OBJECTID", "ASSETID", "CONDITION"}


# --- retry on temporary HTTP failure -------------------------------------------

def test_discover_layers_retries_transient_failures_then_succeeds():
    service = FakeArcGISService(NESTED_DISCOVERY, {})
    attempts = {"count": 0}
    real_opener = service.opener

    def flaky_opener(resource, timeout):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise http.client.HTTPException("simulated transient failure")
        return real_opener(resource, timeout=timeout)

    layers = discover_layers(SERVICE_URL, opener=flaky_opener)
    assert attempts["count"] == 3
    assert len(layers) == 5


def test_discover_layers_gives_up_after_exhausting_retries():
    def always_fails(resource, timeout):
        raise OSError("simulated permanent failure")

    with pytest.raises(Exception, match="ArcGIS service discovery failed"):
        discover_layers(SERVICE_URL, opener=always_fails)


# --- full pipeline + manifest --------------------------------------------------

def test_download_arcgis_layers_writes_gpkg_and_manifest(tmp_path):
    pages = [{"type": "FeatureCollection", "crs": _crs(), "features": [
        _catch_basin_feature(1, "CB-1", "Good", -118.1, 34.1),
        _catch_basin_feature(2, "CB-2", None, -118.2, 34.2),
    ]}]
    service = FakeArcGISService(NESTED_DISCOVERY, {1: CATCH_BASIN_METADATA}, {1: pages})
    output = tmp_path / "stormwater.gpkg"
    manifest_path = tmp_path / "stormwater.manifest.json"

    manifest = download_arcgis_layers(
        SERVICE_URL, output, names=["Catch Basins"],
        manifest_path=manifest_path, opener=service.opener,
    )

    assert manifest["provider"] == "arcgis_rest"
    assert manifest["source"] == SERVICE_URL
    assert len(manifest["layers"]) == 1
    record = manifest["layers"][0]
    assert record["feature_count"] == 2
    assert record["crs"] == "EPSG:4326"
    assert record["layer_metadata"]["layer_id"] == 1
    assert record["layer_metadata"]["name"] == "Catch Basins"
    assert record["source_url"].endswith("/1/query")
    assert "resultOffset" not in record["query_parameters"]  # stable template, not a specific page
    assert manifest["downloaded_at"]

    frame = gpd.read_file(output, layer=record["output_layer"])
    assert len(frame) == 2

    on_disk = json.loads(manifest_path.read_text())
    assert on_disk == manifest

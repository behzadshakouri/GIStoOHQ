"""Tests for the shared provenance manifest used by the sources downloaders."""

from __future__ import annotations

import json

import pytest

from ohqbuilder.sources.manifest import (
    SourceLayerRecord,
    build_manifest,
    write_manifest,
)


def _record(output_layer="catch_basins"):
    return SourceLayerRecord(
        output_layer=output_layer,
        source_url="https://example.gov/arcgis/rest/services/Storm/FeatureServer/1/query",
        query_parameters={"where": "1=1", "outFields": "*", "f": "geojson"},
        feature_count=42,
        crs="EPSG:4326",
        layer_metadata={"layer_id": 1, "name": "Catch Basins"},
    )


def test_build_manifest_requires_at_least_one_record():
    with pytest.raises(ValueError, match="at least one"):
        build_manifest(provider="arcgis_rest", source="https://example.gov", output_path="out.gpkg", records=[])


def test_build_manifest_includes_all_required_fields():
    manifest = build_manifest(
        provider="arcgis_rest",
        source="https://example.gov/arcgis/rest/services/Storm/FeatureServer",
        output_path="/tmp/storm.gpkg",
        records=[_record()],
        downloaded_at="2026-01-01T00:00:00Z",
    )
    assert manifest["provider"] == "arcgis_rest"
    assert manifest["downloaded_at"] == "2026-01-01T00:00:00Z"
    assert manifest["output_path"] == "/tmp/storm.gpkg"
    assert len(manifest["layers"]) == 1
    layer = manifest["layers"][0]
    assert layer == {
        "output_layer": "catch_basins",
        "source_url": "https://example.gov/arcgis/rest/services/Storm/FeatureServer/1/query",
        "query_parameters": {"where": "1=1", "outFields": "*", "f": "geojson"},
        "feature_count": 42,
        "crs": "EPSG:4326",
        "layer_metadata": {"layer_id": 1, "name": "Catch Basins"},
    }


def test_build_manifest_merges_extra_fields_without_overriding_schema():
    manifest = build_manifest(
        provider="socrata", source="example.gov/abcd-1234", output_path="out.gpkg",
        records=[_record()], extra={"service_root": "https://example.gov"},
    )
    assert manifest["service_root"] == "https://example.gov"
    assert manifest["provider"] == "socrata"


def test_write_manifest_is_atomic_and_round_trips(tmp_path):
    manifest = build_manifest(
        provider="arcgis_rest", source="https://example.gov", output_path="out.gpkg",
        records=[_record()], downloaded_at="2026-01-01T00:00:00Z",
    )
    target = tmp_path / "nested" / "out.manifest.json"
    written = write_manifest(target, manifest)
    assert written == target
    assert not list(target.parent.glob(".*"))  # no leftover temp file
    on_disk = json.loads(target.read_text())
    assert on_disk == manifest


def test_write_manifest_overwrites_an_existing_file(tmp_path):
    target = tmp_path / "out.manifest.json"
    first = build_manifest(
        provider="arcgis_rest", source="a", output_path="out.gpkg", records=[_record("a")],
    )
    second = build_manifest(
        provider="arcgis_rest", source="b", output_path="out.gpkg", records=[_record("b")],
    )
    write_manifest(target, first)
    write_manifest(target, second)
    on_disk = json.loads(target.read_text())
    assert on_disk["source"] == "b"

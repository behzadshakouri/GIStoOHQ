import io
import json

import pytest

from ohqbuilder.cli import main
from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.schemas import (
    SiteSpec,
    WatershedDataError,
    canonical_request_key,
)


def test_request_identity_is_canonical_and_separate_from_content():
    first = canonical_request_key("usgs", "https://example.test", {"b": 2, "a": 1}, "1")
    second = canonical_request_key("usgs", "https://example.test", {"a": 1, "b": 2}, "1")
    changed = canonical_request_key("usgs", "https://example.test", {"a": 2, "b": 2}, "1")

    assert first == second
    assert first != changed


def test_site_spec_requires_timezone_and_valid_period():
    with pytest.raises(WatershedDataError, match="timezone"):
        SiteSpec.from_dict(
            {
                "site_id": "test",
                "geometry": {"outlet": {"longitude": -77, "latitude": 39}},
                "study_period": {"start": "2020-01-01", "end": "2021-01-01T00:00:00Z"},
            }
        )


def test_object_store_deduplicates_and_catalog_registers_once(tmp_path):
    store = ObjectStore(tmp_path / "cache")
    first = store.put(io.BytesIO(b"weather data"))
    second = store.put(io.BytesIO(b"weather data"))
    assert first.content_digest == second.content_digest
    assert first.path == second.path
    assert first.path.read_bytes() == b"weather data"

    catalog = AssetCatalog(tmp_path / "catalog.json")
    asset = {
        "provider": "example",
        "product": "weather",
        "content_digest": first.content_digest,
        "size": first.size,
        "media_type": "text/csv",
    }
    catalog.register(asset)
    catalog.register(asset)
    assert len(json.loads((tmp_path / "catalog.json").read_text())["assets"]) == 1


def test_data_cli_creates_and_validates_site_without_affecting_full_run(tmp_path, capsys):
    path = tmp_path / "site.yaml"
    status = main(
        [
            "data", "init-site", "--site-spec", str(path), "--site-id", "hickey_run",
            "--lon", "-76.98", "--lat", "38.92", "--start", "2018-01-01T00:00:00Z",
            "--end", "2025-12-31T23:00:00Z",
        ]
    )
    assert status == 0
    assert main(["data", "validate-site", "--site-spec", str(path)]) == 0
    assert "SiteSpec valid: hickey_run" in capsys.readouterr().out


def test_existing_full_run_parser_does_not_require_data_options():
    from ohqbuilder.cli import build_parser

    args = build_parser().parse_args(
        ["full-run", "--root", "/tmp/root", "--site", "site", "--lon", "-77", "--lat", "39"]
    )
    assert args.command == "full-run"
    assert not hasattr(args, "site_spec")

import io
from pathlib import Path

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.package import validate_package
from ohqbuilder.watershed_data.pipeline import run_watershed_data_pipeline
from ohqbuilder.watershed_data.workflow import write_site_spec


def _fixture_acquirer(fixture, provider, product):
    def acquire(spec, *args, cache, catalog, **kwargs):
        raw = Path(fixture).read_bytes()
        stored = ObjectStore(cache).put(io.BytesIO(raw))
        return AssetCatalog(catalog).register({
            "provider": provider, "product": product,
            "content_digest": stored.content_digest, "size": stored.size,
            "media_type": "application/json", "processing_status": "native",
        })
    return acquire


def test_run_pipeline_downloads_harmonizes_packages_and_exports(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="test", name="Test", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    result = run_watershed_data_pipeline(
        site_spec=site, station_id="01649500", workspace=tmp_path / "run",
        discharge_acquirer=_fixture_acquirer(
            "tests/fixtures/usgs_discharge.json", "usgs", "observed-discharge"
        ),
        weather_acquirer=_fixture_acquirer(
            "tests/fixtures/nasa_power_hourly.json", "nasa-power", "historical-meteorology"
        ),
        pet_acquirer=_fixture_acquirer(
            "tests/fixtures/nasa_power_hourly.json", "nasa-power", "pet-et"
        ),
        export_hydropinn_profile=True,
    )
    assert len(result["native_asset_ids"]) == 3
    assert len(result["derived_asset_ids"]) == 3
    assert Path(result["package_manifest"]).is_file()
    assert Path(result["hydropinn_manifest"]).is_file()
    assert validate_package(tmp_path / "run" / "watershed_package").package_id == result["package_id"]
    assert len(list((tmp_path / "run" / "watershed_package" / "quality_control").glob("*.json"))) == 3

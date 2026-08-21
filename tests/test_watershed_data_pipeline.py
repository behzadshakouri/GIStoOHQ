import io
from pathlib import Path

import pytest

from ohqbuilder.watershed_data.catalog import AssetCatalog, ObjectStore
from ohqbuilder.watershed_data.package import validate_package
from ohqbuilder.watershed_data.pipeline import run_watershed_data_pipeline
from ohqbuilder.watershed_data.schemas import WatershedDataError
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


def _fixture_forecast_acquirer(*, url, provider, product, cache, catalog, **kwargs):
    raw = Path("tests/fixtures/forecast_archive.json").read_bytes()
    stored = ObjectStore(cache).put(io.BytesIO(raw))
    return AssetCatalog(catalog).register({
        "provider": provider, "product": product, "source_url": url,
        "content_digest": stored.content_digest, "size": stored.size,
        "media_type": "application/json", "processing_status": "native",
    })


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
    assert validate_package(tmp_path / "run" / "watershed_package").package_qc_status == "warning"
    assert len(list((tmp_path / "run" / "watershed_package" / "quality_control").glob("*.json"))) == 3
    manifest = validate_package(tmp_path / "run" / "watershed_package")
    assert any(path.startswith("quality_control/") for path in manifest.sidecar_checksums)
    assert any(path.startswith("provenance/") for path in manifest.sidecar_checksums)

    sidecar = next((tmp_path / "run" / "watershed_package" / "provenance").glob("*.json"))
    original = sidecar.read_bytes()
    sidecar.write_bytes(b"{}")
    try:
        with pytest.raises(WatershedDataError, match="corrupt package sidecar"):
            validate_package(tmp_path / "run" / "watershed_package")
    finally:
        sidecar.write_bytes(original)


def test_weather_pet_pipeline_does_not_require_station_id(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="weather", name="Weather", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    result = run_watershed_data_pipeline(
        site_spec=site, station_id="", workspace=tmp_path / "weather_run",
        include_discharge=False,
        weather_acquirer=_fixture_acquirer(
            "tests/fixtures/nasa_power_hourly.json", "nasa-power", "historical-meteorology"
        ),
        pet_acquirer=_fixture_acquirer(
            "tests/fixtures/nasa_power_hourly.json", "nasa-power", "pet-et"
        ),
        export_hydropinn_profile=True,
    )
    assert len(result["native_asset_ids"]) == 2
    assert Path(result["hydropinn_manifest"]).is_file()


def test_weather_pipeline_bootstraps_missing_site_spec(tmp_path):
    site = tmp_path / "sites" / "weather.yaml"
    result = run_watershed_data_pipeline(
        site_spec=site, station_id="", workspace=tmp_path / "bootstrap_run",
        include_discharge=False, init_if_missing=True, site_id="weather", name="Weather",
        longitude=-77, latitude=39, study_start="2025-01-01T00:00:00Z",
        study_end="2025-01-02T00:00:00Z",
        weather_acquirer=_fixture_acquirer(
            "tests/fixtures/nasa_power_hourly.json", "nasa-power", "historical-meteorology"
        ),
        pet_acquirer=_fixture_acquirer(
            "tests/fixtures/nasa_power_hourly.json", "nasa-power", "pet-et"
        ),
        export_hydropinn_profile=True,
    )
    assert site.is_file()
    assert Path(result["hydropinn_manifest"]).is_file()


def test_pipeline_includes_forecast_archive_and_prediction_time_view(tmp_path):
    site = write_site_spec(
        tmp_path / "site.yaml", site_id="forecast", name="Forecast", longitude=-77, latitude=39,
        start="2025-01-01T00:00:00Z", end="2025-01-02T00:00:00Z",
    )
    result = run_watershed_data_pipeline(
        site_spec=site, station_id="", workspace=tmp_path / "forecast_run",
        include_discharge=False, include_weather=False, include_pet=False,
        forecast_url="https://example.test/forecast.json", forecast_provider="example",
        prediction_time="2025-01-01T03:00:00Z",
        forecast_acquirer=_fixture_forecast_acquirer,
    )
    assert result["forecast_asset_id"] in result["native_asset_ids"]
    assert result["forecast_view_asset_id"] in result["derived_asset_ids"]
    assert validate_package(tmp_path / "forecast_run" / "watershed_package")

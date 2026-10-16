import gzip
import os
from datetime import datetime, timezone

import pytest

from ohqbuilder.watershed_data.mrms import (
    MRMS_PRODUCT_NAMES,
    area_weighted_basin_average,
    download_mrms_hour,
)
from ohqbuilder.watershed_data.schemas import WatershedDataError


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


def test_area_weighted_basin_average_matches_hand_computed_value():
    # Two cells half-inside the basin (weight 50 m^2 each), one fully
    # outside (weight 0, must be excluded) - hand-computed weighted mean:
    # (50*10 + 50*20) / (50+50) = 15.0
    result = area_weighted_basin_average(
        cell_lats=[39.0, 39.0, 39.0],
        cell_lons_180=[-77.0, -77.01, -77.02],
        cell_values=[10.0, 20.0, 999.0],
        cell_weights_m2=[50.0, 50.0, 0.0],
    )
    assert result["mean"] == pytest.approx(15.0)
    assert result["n_cells_used"] == 2
    assert result["weighted_area_m2"] == pytest.approx(100.0)
    assert result["cell_min"] == 10.0
    assert result["cell_max"] == 20.0


def test_area_weighted_basin_average_excludes_negative_sentinel_and_nonfinite():
    # MRMS encodes missing/no-coverage as a large negative sentinel -
    # must not be averaged in as if it were real (negative) rainfall.
    result = area_weighted_basin_average(
        cell_lats=[39.0, 39.0, 39.0],
        cell_lons_180=[-77.0, -77.01, -77.02],
        cell_values=[5.0, -3.0, float("nan")],
        cell_weights_m2=[10.0, 10.0, 10.0],
    )
    assert result["mean"] == pytest.approx(5.0)
    assert result["n_cells_used"] == 1
    assert result["n_negative_sentinel"] == 1
    assert result["n_missing_nonfinite"] == 1


def test_area_weighted_basin_average_returns_none_mean_when_nothing_usable():
    result = area_weighted_basin_average(
        cell_lats=[39.0], cell_lons_180=[-77.0], cell_values=[5.0], cell_weights_m2=[0.0],
    )
    assert result["mean"] is None
    assert result["n_cells_used"] == 0


def test_area_weighted_basin_average_rejects_mismatched_lengths():
    with pytest.raises(WatershedDataError, match="same length"):
        area_weighted_basin_average(
            cell_lats=[39.0, 39.1], cell_lons_180=[-77.0], cell_values=[1.0], cell_weights_m2=[1.0, 1.0],
        )


def test_download_mrms_hour_falls_back_to_renamed_product():
    """Regression test for a real bug hit during the NSE>=0.8 recovery
    program: MRMS renamed GaugeCorr_QPE_01H to MultiSensor_QPE_01H_Pass2
    partway through its history. A first pass that only tried the old
    name got a 404 page for every 2021-2022 hour and silently treated it
    as zero rainfall instead of failing - this test pins the fallback
    behavior that prevents that regression."""
    # Pseudo-random, not repetitive - a real grid doesn't gzip down to a
    # trivial size the way b"\x00" * N would, and the undersized-response
    # check below must not trip on this stand-in for real grid bytes.
    real_grid = os.urandom(150_000)
    gz_real = gzip.compress(real_grid)
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if "GaugeCorr_QPE_01H" in url:
            raise OSError("HTTP Error 404: Not Found")
        return _Response(gz_real)

    raw, product, source_url = download_mrms_hour(
        datetime(2021, 8, 10, 1, tzinfo=timezone.utc), opener=opener,
    )
    assert product == "MultiSensor_QPE_01H_Pass2"
    assert raw == real_grid
    assert len(calls) == 2  # tried GaugeCorr_QPE_01H first (404), then fell back
    assert "GaugeCorr_QPE_01H" in calls[0]
    assert "MultiSensor_QPE_01H_Pass2" in calls[1]


def test_download_mrms_hour_succeeds_on_first_product_without_trying_fallbacks():
    real_grid = os.urandom(150_000)
    gz_real = gzip.compress(real_grid)
    calls = []

    def opener(url, timeout):
        calls.append(url)
        return _Response(gz_real)

    raw, product, source_url = download_mrms_hour(
        datetime(2020, 5, 29, 20, tzinfo=timezone.utc), opener=opener,
    )
    assert product == MRMS_PRODUCT_NAMES[0]
    assert len(calls) == 1


def test_download_mrms_hour_rejects_undersized_response_as_likely_error_page():
    """A 404/error HTML page is a valid HTTP response (no exception from
    download_bytes) but far smaller than any real compressed CONUS grid -
    must be rejected on size, not accepted as zero/empty data."""
    tiny_html = b"<html>404 Not Found</html>"

    def opener(url, timeout):
        return _Response(tiny_html)

    with pytest.raises(WatershedDataError, match="unavailable"):
        download_mrms_hour(datetime(2099, 1, 1, tzinfo=timezone.utc), opener=opener)


def test_download_mrms_hour_raises_with_all_failure_reasons_when_every_product_fails():
    def opener(url, timeout):
        raise OSError("HTTP Error 404: Not Found")

    with pytest.raises(WatershedDataError) as excinfo:
        download_mrms_hour(datetime(2099, 1, 1, tzinfo=timezone.utc), opener=opener)
    for product in MRMS_PRODUCT_NAMES:
        assert product in str(excinfo.value)

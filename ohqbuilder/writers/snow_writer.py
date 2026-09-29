from __future__ import annotations

import math
import os

from ..model.watershed import Watershed
from .rainfall_writer import rainfall_filename


def temperature_filename() -> str | None:
    """Return the explicitly configured air-temperature forcing file."""

    return os.environ.get("OHQ_TEMPERATURE_FILE", "").strip() or None


def _validate_filename(value: str, variable: str) -> None:
    if any(char in value for char in ",;\n\r"):
        raise ValueError(f"{variable} contains an OHQ command delimiter")


def _optional_finite_environment(variable: str) -> float | None:
    text = os.environ.get(variable, "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"{variable} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{variable} must be a finite number")
    return value


def temperature_adjustment_settings() -> tuple[float | None, float]:
    """Return reference elevation and environmental lapse rate."""

    reference = _optional_finite_environment(
        "OHQ_TEMPERATURE_REFERENCE_ELEVATION_M"
    )
    configured_lapse = _optional_finite_environment(
        "OHQ_TEMPERATURE_LAPSE_RATE_C_PER_KM"
    )
    if reference is None and configured_lapse is not None:
        raise ValueError(
            "OHQ_TEMPERATURE_LAPSE_RATE_C_PER_KM requires "
            "OHQ_TEMPERATURE_REFERENCE_ELEVATION_M"
        )
    return reference, configured_lapse if configured_lapse is not None else -6.5


def temperature_offset_c(
    elevation_m: float,
    reference_elevation_m: float | None,
    lapse_rate_c_per_km: float,
) -> float:
    """Calculate the additive air-temperature offset for one subbasin."""

    if reference_elevation_m is None:
        return 0.0
    return lapse_rate_c_per_km * (elevation_m - reference_elevation_m) / 1000.0


def snow_source_names(subbasin_name: str) -> tuple[str, str]:
    return f"LiquidRain_{subbasin_name}", f"Snowfall_{subbasin_name}"


def air_temperature_lines(temperature: str) -> list[str]:
    _validate_filename(temperature, "OHQ_TEMPERATURE_FILE")
    return [
        "create source;type=Air_Temperature,name=AirTemperature,"
        f"timeseries={temperature}"
    ]


def snow_partition_lines(
    watershed: Watershed,
    temperature: str,
    subbasin_name: str,
    temperature_offset: float,
) -> list[str]:
    """Create complementary liquid-rain and snowfall sources for one basin."""

    precipitation = rainfall_filename(watershed)
    _validate_filename(temperature, "OHQ_TEMPERATURE_FILE")
    if precipitation:
        _validate_filename(precipitation, "OHQ_RAINFALL_FILE")
    forcing = f",timeseries={precipitation}" if precipitation else ""
    liquid_name, snowfall_name = snow_source_names(subbasin_name)
    offset = f"{temperature_offset:.12g}"
    return [
        f"create source;type=Liquid_Precipitation,name={liquid_name}"
        f"{forcing},Temperature={temperature},temperature_offset={offset}",
        f"create source;type=Snowfall,name={snowfall_name}"
        f"{forcing},Temperature={temperature},temperature_offset={offset}",
    ]

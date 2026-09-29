from __future__ import annotations

import os

from ..model.watershed import Watershed
from .rainfall_writer import rainfall_filename


def temperature_filename() -> str | None:
    """Return the explicitly configured air-temperature forcing file."""

    return os.environ.get("OHQ_TEMPERATURE_FILE", "").strip() or None


def snow_forcing_lines(watershed: Watershed, temperature: str) -> list[str]:
    """Create complementary rain/snow sources and shared melt temperature."""

    for value, variable in (
        (temperature, "OHQ_TEMPERATURE_FILE"),
        (rainfall_filename(watershed) or "", "OHQ_RAINFALL_FILE"),
    ):
        if any(char in value for char in ",;\n\r"):
            raise ValueError(f"{variable} contains an OHQ command delimiter")

    precipitation = rainfall_filename(watershed)
    forcing = f",timeseries={precipitation}" if precipitation else ""
    return [
        "create source;type=Liquid_Precipitation,name=LiquidRain"
        f"{forcing},Temperature={temperature}",
        "create source;type=Snowfall,name=Snowfall"
        f"{forcing},Temperature={temperature}",
        "create source;type=Air_Temperature,name=AirTemperature,"
        f"timeseries={temperature}",
    ]

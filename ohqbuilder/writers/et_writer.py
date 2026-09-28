from __future__ import annotations

import os


def et_filename() -> str | None:
    """A prepared OpenHydroQual time series of nonnegative ET depths in m/day.

    NASA POWER's native EVPTRNS may instead be an energy flux and must not be
    wired directly into a depth-rate source without a documented conversion.
    """
    return os.environ.get("OHQ_ET_FILE", "").strip() or None


def et_lines(filename: str) -> list[str]:
    if any(char in filename for char in ",;\n\r"):
        raise ValueError("OHQ_ET_FILE contains an OHQ command delimiter")
    return [
        "create source;type=Evapotranspiration_Time_Series (Soil),"
        f"name=ET,ET_timeseries={filename}"
    ]

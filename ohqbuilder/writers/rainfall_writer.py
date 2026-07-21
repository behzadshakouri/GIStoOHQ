from __future__ import annotations

import os
from pathlib import Path

from ..model.watershed import Watershed


def _safe_model_name(value: str) -> str:
    text = str(value or "Watershed").replace(";", "_").replace(",", "_").strip()
    return text or "Watershed"


def rainfall_filename(watershed: Watershed) -> str:
    """Return the rainfall time-series filename referenced by the OHQ model.

    ``OHQ_RAINFALL_FILE`` may be exported by a project runner.  Otherwise the
    writer references ``<watershed>_rainfall.txt`` beside the generated model.
    The file must use a format accepted by OpenHydroQual's Precipitation source.
    """

    configured = os.environ.get("OHQ_RAINFALL_FILE", "").strip()
    if configured:
        return configured
    return f"{_safe_model_name(watershed.name)}_rainfall.txt"


def rainfall_lines(watershed: Watershed) -> list[str]:
    """Return native OpenHydroQual precipitation-source command lines."""

    filename = rainfall_filename(watershed)
    return [
        (
            "create source;"
            "type=Precipitation,"
            "name=Rain,"
            f"timeseries={filename}"
        )
    ]

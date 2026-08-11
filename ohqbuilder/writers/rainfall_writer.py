from __future__ import annotations

import os

from ..model.watershed import Watershed


def rainfall_filename(_watershed: Watershed) -> str | None:
    """Return an explicitly configured OHQ precipitation file, if any.

    GIStoOHQ must not invent a filename: doing so makes OpenHydroQual try to
    parse a file that the builder did not create.  ``OHQ_RAINFALL_FILE`` must
    therefore name a real file in OpenHydroQual's Precipitation-source format.
    Without it, the source is created unassigned so the model remains loadable
    and precipitation can be selected in OpenHydroQual later.
    """

    configured = os.environ.get("OHQ_RAINFALL_FILE", "").strip()
    if configured:
        return configured
    return None


def rainfall_lines(watershed: Watershed) -> list[str]:
    """Return native OpenHydroQual precipitation-source command lines."""

    filename = rainfall_filename(watershed)
    command = "create source;type=Precipitation,name=Rain"
    if filename:
        command += f",timeseries={filename}"
    return [command]

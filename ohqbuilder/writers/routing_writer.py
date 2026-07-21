from __future__ import annotations

import math
from typing import Any

from ..model.reach import Reach


def _number(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def routing_lines(reach: Reach) -> list[str]:
    """Return native OHQ properties for a reach represented as a routing node.

    GIStoOHQ presently represents every GIS reach as a small ``Catch basin``
    routing block, and connects those nodes with ``Sewer_pipe`` links.  This
    uses component names available in OpenHydroQual's Sewer_system template
    and avoids the fictitious ``TrapezoidalChannel`` pseudo-type formerly
    emitted by the project.

    The function is retained for API compatibility with the previous writer.
    """

    z_up = _number(getattr(reach, "z_up_m", None), 0.0)
    z_dn = _number(getattr(reach, "z_dn_m", None), z_up)
    bottom = min(z_up, z_dn)

    return [
        "type=Catch basin",
        "area=1[m~^2]",
        f"bottom_elevation={bottom:.12g}[m]",
        "inflow=",
    ]


def sewer_pipe_properties(reach: Reach) -> list[tuple[str, str]]:
    """Return conservative Sewer_pipe properties derived from a GIS reach."""

    length = max(_number(getattr(reach, "length_m", None), 1.0), 0.01)
    z_up = _number(getattr(reach, "z_up_m", None), 0.0)
    z_dn = _number(getattr(reach, "z_dn_m", None), z_up - 0.001)
    manning = max(_number(getattr(reach, "manning_n", None), 0.035), 1.0e-6)

    width = _number(getattr(reach, "base_width_m", None), 1.0)
    diameter = min(max(width, 0.1), 10.0)

    # Keep a tiny downhill drop when GIS attributes are flat or reversed.  The
    # original values remain available in the GIS products for later hydraulic
    # refinement.
    if z_dn >= z_up:
        z_dn = z_up - max(0.001, 0.0001 * length)

    return [
        ("ManningCoeff", f"{manning:.12g}"),
        ("diameter", f"{diameter:.12g}[m]"),
        ("length", f"{length:.12g}[m]"),
        ("start_elevation", f"{z_up:.12g}[m]"),
        ("end_elevation", f"{z_dn:.12g}[m]"),
    ]

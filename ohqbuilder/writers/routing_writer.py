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
    """Return legacy routing-node properties for compatibility.

    The current OHQ writer represents GIS reaches as ``Sewer_pipe`` links, not
    as ``Catch basin`` blocks.  This function is retained because other code may
    still import it.
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
    """Return ``Sewer_pipe`` properties derived from one GIS reach."""

    length = max(_number(getattr(reach, "length_m", None), 1.0), 0.01)
    z_up = _number(getattr(reach, "z_up_m", None), 0.0)
    z_dn = _number(getattr(reach, "z_dn_m", None), z_up - 0.001)
    manning = max(_number(getattr(reach, "manning_n", None), 0.035), 1.0e-6)

    width = _number(getattr(reach, "base_width_m", None), 1.0)
    diameter = min(max(width, 0.1), 10.0)

    # OpenHydroQual requires a downhill pipe. Preserve GIS values when they are
    # valid; otherwise impose only a very small fall.
    if z_dn >= z_up:
        z_dn = z_up - max(0.001, 0.0001 * length)

    return [
        ("ManningCoeff", f"{manning:.12g}"),
        ("diameter", f"{diameter:.12g}[m]"),
        ("length", f"{length:.12g}[m]"),
        ("start_elevation", f"{z_up:.12g}[m]"),
        ("end_elevation", f"{z_dn:.12g}[m]"),
    ]


def default_pipe_properties() -> list[tuple[str, str]]:
    """Return conservative properties for a routing connection without a reach."""

    return [
        ("ManningCoeff", "0.035"),
        ("diameter", "1[m]"),
        ("length", "1[m]"),
        ("start_elevation", "0.001[m]"),
        ("end_elevation", "0[m]"),
    ]

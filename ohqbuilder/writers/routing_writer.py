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


def trapezoidal_channel_properties(reach: Reach) -> list[tuple[str, str]]:
    """Return OpenHydroQual properties for one trapezoidal stream reach.

    GIS reaches are represented as ``Trapezoidal Channel Segment`` blocks from
    ``open_channel.json``.  Attribute names are intentionally permissive so the
    writer can use values produced by different GIS extraction workflows.

    Recognized reach attributes
    ---------------------------
    length_m
        Channel-segment length.
    base_width_m or width_m
        Bottom width of the trapezoidal section.
    side_slope or side_slope_hv
        Horizontal-to-vertical bank slope, e.g. 2 for 2H:1V.
    manning_n
        Manning roughness coefficient.
    z_up_m, z_dn_m, elevation_m, or z_m
        Channel-bottom elevations.
    initial_depth_m or depth_m
        Initial water depth.
    """

    length = max(_number(getattr(reach, "length_m", None), 1.0), 0.01)

    base_width = _number(
        getattr(reach, "base_width_m", None),
        _number(getattr(reach, "width_m", None), 1.0),
    )
    base_width = max(base_width, 0.05)

    side_slope = _number(
        getattr(reach, "side_slope", None),
        _number(getattr(reach, "side_slope_hv", None), 2.0),
    )
    side_slope = max(side_slope, 0.01)

    manning = max(
        _number(getattr(reach, "manning_n", None), 0.035),
        1.0e-6,
    )

    z_up = _number(
        getattr(reach, "z_up_m", None),
        _number(
            getattr(reach, "elevation_m", None),
            _number(getattr(reach, "z_m", None), 0.0),
        ),
    )
    z_dn = _number(getattr(reach, "z_dn_m", None), z_up)

    # The block represents the reach as a storage segment.  Use the lower end
    # as its bottom elevation so the downstream hydraulic gradient is not
    # artificially reversed.
    bottom_elevation = min(z_up, z_dn)

    initial_depth = _number(
        getattr(reach, "initial_depth_m", None),
        _number(getattr(reach, "depth_m", None), 0.01),
    )
    initial_depth = max(initial_depth, 0.0)

    return [
        ("base_width", f"{base_width:.12g}[m]"),
        ("side_slope", f"{side_slope:.12g}"),
        ("ManningCoeff", f"{manning:.12g}"),
        ("bottom_elevation", f"{bottom_elevation:.12g}[m]"),
        ("depth", f"{initial_depth:.12g}[m]"),
        ("length", f"{length:.12g}[m]"),
        ("inflow", ""),
        ("ag_area", "0[m~^2]"),
        ("non_ag_area", "0[m~^2]"),
        ("ag_withdrawal_per_unit_area", ""),
        ("non_ag_withdrawal_per_unit_area", ""),
        ("dam_height", "0[m]"),
    ]


# Backward-compatible alias for code that imported the old helper name.
def sewer_pipe_properties(reach: Reach) -> list[tuple[str, str]]:
    return trapezoidal_channel_properties(reach)


def default_channel_properties() -> list[tuple[str, str]]:
    """Return conservative defaults for a synthetic channel segment."""

    return [
        ("base_width", "1[m]"),
        ("side_slope", "2"),
        ("ManningCoeff", "0.035"),
        ("bottom_elevation", "0[m]"),
        ("depth", "0.01[m]"),
        ("length", "1[m]"),
        ("inflow", ""),
        ("ag_area", "0[m~^2]"),
        ("non_ag_area", "0[m~^2]"),
        ("ag_withdrawal_per_unit_area", ""),
        ("non_ag_withdrawal_per_unit_area", ""),
        ("dam_height", "0[m]"),
    ]


# Retained only so older imports do not fail. Open-channel connectors obtain
# their hydraulic properties from the connected channel blocks.
def default_pipe_properties() -> list[tuple[str, str]]:
    return []

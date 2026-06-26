from __future__ import annotations
from ..model.reach import Reach

def routing_lines(reach: Reach) -> list[str]:
    return [
        "Type: TrapezoidalChannel",
        f"Length_m: {reach.length_m if reach.length_m is not None else ''}",
        f"Slope: {reach.slope if reach.slope is not None else ''}",
        f"BaseWidth_m: {reach.base_width_m if reach.base_width_m is not None else ''}",
        f"SideSlope_z: {reach.side_slope_z if reach.side_slope_z is not None else ''}",
        f"ManningN: {reach.manning_n if reach.manning_n is not None else ''}",
        f"Z_up_m: {reach.z_up_m if reach.z_up_m is not None else ''}",
        f"Z_dn_m: {reach.z_dn_m if reach.z_dn_m is not None else ''}",
        f"Downstream: {reach.downstream or ''}",
    ]

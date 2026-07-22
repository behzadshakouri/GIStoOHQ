from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Reach:
    id: int
    name: str
    length_m: float | None = None
    slope: float | None = None
    base_width_m: float | None = None
    side_slope_z: float | None = None
    manning_n: float | None = None
    z_up_m: float | None = None
    z_dn_m: float | None = None
    downstream: str | None = None
    x_act: float | None = None
    y_act: float | None = None
    x_up_act: float | None = None
    y_up_act: float | None = None
    x_dn_act: float | None = None
    y_dn_act: float | None = None
    crs_authid: str | None = None
    layout_source: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

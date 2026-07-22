from __future__ import annotations
from dataclasses import dataclass

@dataclass
class TopologyLink:
    element_id: int
    element_type: str
    name: str
    ds_type: str
    ds_id: int | None
    ds_name: str | None
    match_dist_m: float | None = None
    note: str = ""
    x_act: float | None = None
    y_act: float | None = None
    x_up_act: float | None = None
    y_up_act: float | None = None
    x_dn_act: float | None = None
    y_dn_act: float | None = None
    crs_authid: str | None = None
    layout_source: str = ""

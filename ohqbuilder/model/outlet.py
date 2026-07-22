from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Outlet:
    name: str = "Outlet"
    x_act: float | None = None
    y_act: float | None = None
    crs_authid: str | None = None
    layout_source: str = ""

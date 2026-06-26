from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Subbasin:
    id: int
    name: str
    area_km2: float | None = None
    curve_number: float | None = None
    slope_pct: float | None = None
    flow_len_ft: float | None = None
    tc_min: float | None = None
    lag_min: float | None = None
    centroid_x: float | None = None
    centroid_y: float | None = None
    downstream: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

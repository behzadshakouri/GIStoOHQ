from __future__ import annotations
from pathlib import Path
from ..model.subbasin import Subbasin
from ..utils.naming import subbasin_name
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

class SubbasinReader:
    def __init__(self, layer: str = "subwatershed_params"):
        self.layer = layer

    def read(self, path: Path) -> list[Subbasin]:
        df = read_layer(path, self.layer)
        out = []
        for _, row in df.iterrows():
            sid = safe_int(row_get(row, "id"), 0)
            out.append(Subbasin(
                id=sid,
                name=subbasin_name(sid),
                area_km2=safe_float(row_get(row, "area_km2")),
                curve_number=safe_float(row_get(row, "CN")),
                slope_pct=safe_float(row_get(row, "slope_pct")),
                flow_len_ft=safe_float(row_get(row, "flow_len_ft")),
                tc_min=safe_float(row_get(row, "tc_min")),
                lag_min=safe_float(row_get(row, "lag_min")),
                centroid_x=safe_float(row_get(row, "centroid_x")),
                centroid_y=safe_float(row_get(row, "centroid_y")),
                attributes={k: row_get(row, k) for k in getattr(df, "columns", []) if k != "geometry"},
            ))
        return out

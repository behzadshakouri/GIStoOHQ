from __future__ import annotations
from pathlib import Path
from ..model.subbasin import Subbasin
from ..utils.naming import subbasin_name
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

def _crs_text(df):
    if getattr(df, "crs", None) is None:
        return None
    authority = df.crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else str(df.crs)

class SubbasinReader:
    def __init__(self, layer: str = "subwatershed_params"):
        self.layer = layer

    def read(self, path: Path) -> list[Subbasin]:
        df = read_layer(path, self.layer)
        crs_authid = _crs_text(df)
        out = []
        for _, row in df.iterrows():
            sid = safe_int(row_get(row, "id"), 0)
            x = safe_float(row_get(row, "centroid_x"))
            y = safe_float(row_get(row, "centroid_y"))
            geom = row_get(row, "geometry")
            if (x is None or y is None) and geom is not None and not geom.is_empty:
                point = geom.representative_point()
                x, y = float(point.x), float(point.y)
            out.append(Subbasin(
                id=sid,
                name=subbasin_name(sid),
                area_km2=safe_float(row_get(row, "area_km2")),
                curve_number=safe_float(row_get(row, "CN")),
                slope_pct=safe_float(row_get(row, "slope_pct")),
                flow_len_ft=safe_float(row_get(row, "flow_len_ft")),
                tc_min=safe_float(row_get(row, "tc_min")),
                lag_min=safe_float(row_get(row, "lag_min")),
                centroid_x=x,
                centroid_y=y,
                x_act=x,
                y_act=y,
                crs_authid=crs_authid,
                layout_source="subbasin_centroid",
                attributes={k: row_get(row, k) for k in getattr(df, "columns", []) if k != "geometry"},
            ))
        return out

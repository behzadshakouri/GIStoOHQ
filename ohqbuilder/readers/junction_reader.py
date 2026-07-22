from __future__ import annotations
from pathlib import Path
from ..model.junction import Junction
from ..utils.naming import junction_name
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

def _crs_text(df):
    if getattr(df, "crs", None) is None:
        return None
    authority = df.crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else str(df.crs)

class JunctionReader:
    def __init__(self, layer: str = "junctions"):
        self.layer = layer

    def read(self, path: Path) -> list[Junction]:
        df = read_layer(path, self.layer)
        crs_authid = _crs_text(df)
        out = []
        for _, row in df.iterrows():
            jid = safe_int(row_get(row, "junction_id"), 0)
            x = safe_float(row_get(row, "x"))
            y = safe_float(row_get(row, "y"))
            geom = row_get(row, "geometry")
            if (x is None or y is None) and geom is not None and not geom.is_empty:
                point = geom.representative_point()
                x, y = float(point.x), float(point.y)
            out.append(Junction(
                id=jid,
                name=junction_name(jid),
                x=x,
                y=y,
                x_act=x,
                y_act=y,
                crs_authid=crs_authid,
                layout_source="junction_geometry",
                attributes={k: row_get(row, k) for k in getattr(df, "columns", []) if k != "geometry"},
            ))
        return out

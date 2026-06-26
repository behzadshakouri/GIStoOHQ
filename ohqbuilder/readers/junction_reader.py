from __future__ import annotations
from pathlib import Path
from ..model.junction import Junction
from ..utils.naming import junction_name
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

class JunctionReader:
    def __init__(self, layer: str = "junctions"):
        self.layer = layer

    def read(self, path: Path) -> list[Junction]:
        df = read_layer(path, self.layer)
        out = []
        for _, row in df.iterrows():
            jid = safe_int(row_get(row, "junction_id"), 0)
            out.append(Junction(
                id=jid,
                name=junction_name(jid),
                x=safe_float(row_get(row, "x")),
                y=safe_float(row_get(row, "y")),
                attributes={k: row_get(row, k) for k in getattr(df, "columns", []) if k != "geometry"},
            ))
        return out

from __future__ import annotations
from pathlib import Path
from ..model.reach import Reach
from ..utils.naming import reach_name
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

class ReachReader:
    def read(self, path: Path) -> list[Reach]:
        df = read_layer(path)
        out = []
        for _, row in df.iterrows():
            rid = safe_int(row_get(row, "reach_id"), 0)
            out.append(Reach(
                id=rid,
                name=reach_name(rid),
                length_m=safe_float(row_get(row, "length_m")),
                slope=safe_float(row_get(row, "slope_mm")),
                base_width_m=safe_float(row_get(row, "base_w_m")),
                side_slope_z=safe_float(row_get(row, "side_z")),
                manning_n=safe_float(row_get(row, "manning_n")),
                z_up_m=safe_float(row_get(row, "z_up_m")),
                z_dn_m=safe_float(row_get(row, "z_dn_m")),
                attributes={k: row_get(row, k) for k in getattr(df, "columns", []) if k != "geometry"},
            ))
        return out

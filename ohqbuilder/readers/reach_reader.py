from __future__ import annotations
from pathlib import Path
from typing import Any
from ..model.reach import Reach
from ..utils.naming import reach_name
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

ORIENTATION_REVERSAL_MIN_RISE_M = 0.25
ORIENTATION_REVERSAL_MIN_SLOPE = 0.00010

def _crs_text(df):
    if getattr(df, "crs", None) is None:
        return None
    authority = df.crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else str(df.crs)

def _longest_line_part(geometry: Any):
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type == "LineString":
        return geometry
    if geometry.geom_type == "MultiLineString":
        parts = [part for part in geometry.geoms if not part.is_empty]
        return max(parts, key=lambda part: part.length) if parts else None
    return None

class ReachReader:
    def read(self, path: Path) -> list[Reach]:
        df = read_layer(path)
        crs_authid = _crs_text(df)
        out = []
        for _, row in df.iterrows():
            rid = safe_int(row_get(row, "reach_id"), 0)
            line = _longest_line_part(row_get(row, "geometry"))
            x_act = y_act = x_up = y_up = x_dn = y_dn = None
            layout_source = ""
            if line is not None and line.length > 0:
                coords = list(line.coords)
                first_x, first_y = map(float, coords[0][:2])
                last_x, last_y = map(float, coords[-1][:2])
                orient_text = str(row_get(row, "topo_orient", "") or "").strip().lower()
                swap = orient_text == "strong-elevation-reversal"
                if not orient_text:
                    z_first = safe_float(row_get(row, "z_up_m"))
                    z_last = safe_float(row_get(row, "z_dn_m"))
                    if z_first is not None and z_last is not None:
                        adverse_rise = z_last - z_first
                        adverse_slope = adverse_rise / float(line.length)
                        swap = adverse_rise >= ORIENTATION_REVERSAL_MIN_RISE_M and adverse_slope >= ORIENTATION_REVERSAL_MIN_SLOPE
                if swap:
                    x_up, y_up, x_dn, y_dn = last_x, last_y, first_x, first_y
                else:
                    x_up, y_up, x_dn, y_dn = first_x, first_y, last_x, last_y
                midpoint = line.interpolate(line.length / 2.0)
                x_act, y_act = float(midpoint.x), float(midpoint.y)
                layout_source = "reach_geometry_midpoint"
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
                x_act=x_act,
                y_act=y_act,
                x_up_act=x_up,
                y_up_act=y_up,
                x_dn_act=x_dn,
                y_dn_act=y_dn,
                crs_authid=crs_authid,
                layout_source=layout_source,
                attributes={k: row_get(row, k) for k in getattr(df, "columns", []) if k != "geometry"},
            ))
        return out

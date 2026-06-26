from __future__ import annotations
from pathlib import Path
from ..model.topology import TopologyLink
from ..utils.units import safe_float, safe_int
from .gpkg_utils import read_layer, row_get

class TopologyReader:
    def __init__(self, layer: str = "topology"):
        self.layer = layer

    def read(self, path: Path) -> list[TopologyLink]:
        df = read_layer(path, self.layer)
        links = []
        for _, row in df.iterrows():
            links.append(TopologyLink(
                element_id=safe_int(row_get(row, "element_id"), 0),
                element_type=str(row_get(row, "element_type", "")),
                name=str(row_get(row, "name", "")),
                ds_type=str(row_get(row, "ds_type", "")),
                ds_id=safe_int(row_get(row, "ds_id")),
                ds_name=row_get(row, "ds_name"),
                match_dist_m=safe_float(row_get(row, "match_dist_m")),
                note=str(row_get(row, "note", "") or ""),
            ))
        return links

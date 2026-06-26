from __future__ import annotations
from ..model.watershed import Watershed

def apply_topology(watershed: Watershed) -> None:
    ds_by_name = {t.name: t.ds_name for t in watershed.topology}
    for obj in watershed.subbasins:
        obj.downstream = ds_by_name.get(obj.name)
    for obj in watershed.reaches:
        obj.downstream = ds_by_name.get(obj.name)
    for obj in watershed.junctions:
        obj.downstream = ds_by_name.get(obj.name)

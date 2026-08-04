from __future__ import annotations
from ..model.watershed import Watershed


def retain_topology_elements(watershed: Watershed) -> None:
    """Remove extracted candidates that Phase 2 did not retain in the model graph.

    ``reaches.gpkg`` and ``junctions.gpkg`` intentionally retain the complete GIS
    extraction for QA. ``topology.gpkg`` is the source of truth for the final
    model, so writers must not serialize extracted elements absent from it.
    """

    if not watershed.topology:
        return
    retained = {"subbasin": set(), "reach": set(), "junction": set()}
    for link in watershed.topology:
        kind = str(link.element_type or "").strip().lower()
        if kind in retained:
            retained[kind].add(link.name)
    watershed.subbasins = [item for item in watershed.subbasins if item.name in retained["subbasin"]]
    watershed.reaches = [item for item in watershed.reaches if item.name in retained["reach"]]
    watershed.junctions = [item for item in watershed.junctions if item.name in retained["junction"]]

def apply_topology(watershed: Watershed) -> None:
    ds_by_name = {t.name: t.ds_name for t in watershed.topology}
    for obj in watershed.subbasins:
        obj.downstream = ds_by_name.get(obj.name)
    for obj in watershed.reaches:
        obj.downstream = ds_by_name.get(obj.name)
    for obj in watershed.junctions:
        obj.downstream = ds_by_name.get(obj.name)

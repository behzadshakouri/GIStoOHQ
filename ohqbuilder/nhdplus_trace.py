from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path


class NhdplusTraceError(RuntimeError):
    """Raised when NHDPlus identifiers or connectivity cannot support a reliable trace."""


def _field(columns, aliases: tuple[str, ...], label: str) -> str:
    lookup = {str(column).lower(): str(column) for column in columns}
    for alias in aliases:
        if alias.lower() in lookup:
            return lookup[alias.lower()]
    raise NhdplusTraceError(f"Missing {label} field; tried {', '.join(aliases)}")


def _trace_upstream_indices(records, outlet_index):
    """Return indices upstream of an outlet from ``(index, from_node, to_node)`` records."""

    ending_at: dict[str, list] = defaultdict(list)
    upstream_node = {}
    for index, from_value, to_value in records:
        ending_at[str(to_value)].append(index)
        upstream_node[index] = str(from_value)
    selected = set()
    queue = deque([outlet_index])
    while queue:
        index = queue.popleft()
        if index in selected:
            continue
        selected.add(index)
        queue.extend(ending_at.get(upstream_node[index], ()))
    return selected


def trace_upstream_catchments(
    flowline_path: str | Path,
    catchment_path: str | Path,
    output_path: str | Path,
    *,
    outlet_lon: float,
    outlet_lat: float,
) -> Path:
    """Trace upstream by NHD FromNode/ToNode and dissolve matching catchments."""

    import geopandas as gpd
    from shapely.geometry import Point

    flowlines = gpd.read_file(flowline_path)
    catchments = gpd.read_file(catchment_path, layer="NHDPlusCatchment_clip")
    if flowlines.empty or catchments.empty:
        raise NhdplusTraceError("Flowline and catchment layers must be non-empty")
    if flowlines.crs is None or catchments.crs is None:
        raise NhdplusTraceError("Flowline and catchment layers must define a CRS")

    flow_id = _field(flowlines.columns, ("NHDPlusID", "COMID", "FEATUREID"), "flowline ID")
    from_node = _field(flowlines.columns, ("FromNode", "FromNodeID"), "upstream node")
    to_node = _field(flowlines.columns, ("ToNode", "ToNodeID"), "downstream node")
    catchment_id = _field(
        catchments.columns, ("FEATUREID", "NHDPlusID", "COMID"), "catchment reach ID"
    )

    metric_crs = flowlines.to_crs("EPSG:4326").estimate_utm_crs()
    if metric_crs is None:
        raise NhdplusTraceError("Could not determine a local projected CRS")
    metric_flowlines = flowlines.to_crs(metric_crs)
    outlet = gpd.GeoSeries([Point(outlet_lon, outlet_lat)], crs="EPSG:4326").to_crs(metric_crs)[0]
    distances = metric_flowlines.geometry.distance(outlet)
    outlet_index = distances.idxmin()
    outlet_distance = float(distances.loc[outlet_index])
    outlet_row = flowlines.loc[outlet_index]

    records = (
        (index, row[from_node], row[to_node]) for index, row in flowlines.iterrows()
    )
    selected_indices = _trace_upstream_indices(records, outlet_index)

    selected_flowlines = flowlines.loc[list(selected_indices)].copy()
    selected_ids = {str(value) for value in selected_flowlines[flow_id]}
    selected_catchments = catchments[
        catchments[catchment_id].map(str).isin(selected_ids)
    ].copy()
    if selected_catchments.empty:
        raise NhdplusTraceError("No catchments match the upstream flowline identifiers")

    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    selected_catchments.to_file(target, layer="upstream_catchments", driver="GPKG")
    boundary = gpd.GeoDataFrame(
        {"outlet_reach_id": [str(outlet_row[flow_id])], "reach_count": [len(selected_ids)]},
        geometry=[selected_catchments.geometry.union_all()],
        crs=selected_catchments.crs,
    )
    boundary.to_file(target, layer="upstream_boundary", driver="GPKG")
    selected_flowlines.to_file(target, layer="upstream_flowlines", driver="GPKG")
    metadata = {
        "outlet_reach_id": str(outlet_row[flow_id]),
        "outlet_snap_distance_m": outlet_distance,
        "flowline_count": len(selected_flowlines),
        "catchment_count": len(selected_catchments),
        "connectivity": f"{from_node}->{to_node}",
        "flowline_id_field": flow_id,
        "catchment_id_field": catchment_id,
    }
    target.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return target

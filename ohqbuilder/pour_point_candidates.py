from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .nhdplus_trace import NhdplusTraceError, _field


class PourPointCandidateError(RuntimeError):
    """Raised when a reviewable NHDPlus pour-point layer cannot be created."""


def _confluence_nodes(records) -> dict[str, list]:
    """Return nodes receiving at least two upstream reaches."""

    incoming = defaultdict(list)
    for reach_id, to_node in records:
        incoming[str(to_node)].append(reach_id)
    return {node: reaches for node, reaches in incoming.items() if len(reaches) >= 2}


def generate_pour_point_candidates(
    upstream_candidate_path: str | Path,
    output_path: str | Path,
    *,
    outlet_lon: float,
    outlet_lat: float,
) -> Path:
    """Create a review layer from NHDPlus tributary confluences plus the outlet."""

    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    flowlines = gpd.read_file(upstream_candidate_path, layer="upstream_flowlines")
    if flowlines.empty or flowlines.crs is None:
        raise PourPointCandidateError("Upstream flowlines must be non-empty and define a CRS")
    try:
        reach_id = _field(flowlines.columns, ("NHDPlusID", "COMID", "FEATUREID"), "reach ID")
        from_node = _field(flowlines.columns, ("FromNode", "FromNodeID"), "upstream node")
        to_node = _field(flowlines.columns, ("ToNode", "ToNodeID"), "downstream node")
    except NhdplusTraceError as exc:
        raise PourPointCandidateError(str(exc)) from exc

    metric_crs = flowlines.to_crs("EPSG:4326").estimate_utm_crs()
    if metric_crs is None:
        raise PourPointCandidateError("Could not determine a local projected CRS")
    metric = flowlines.to_crs(metric_crs)
    confluences = _confluence_nodes(zip(metric[reach_id], metric[to_node]))
    downstream_by_node = defaultdict(list)
    for index, node in metric[from_node].items():
        downstream_by_node[str(node)].append(index)

    rows = []
    geometries = []
    for node, incoming_ids in sorted(confluences.items()):
        outgoing = downstream_by_node.get(node, [])
        incoming = metric[metric[reach_id].isin(incoming_ids)]
        if outgoing:
            downstream_geometry = metric.loc[outgoing[0]].geometry
            points = [nearest_points(geometry, downstream_geometry)[0] for geometry in incoming.geometry]
        else:
            points = [geometry.boundary.geoms[-1] for geometry in incoming.geometry]
        geometries.append(Point(sum(point.x for point in points) / len(points), sum(point.y for point in points) / len(points)))
        rows.append(
            {
                "candidate_id": f"confluence-{node}",
                "reason": "tributary_confluence",
                "node_id": node,
                "n_in": len(incoming_ids),
                "review_status": "pending",
            }
        )

    outlet = gpd.GeoSeries([Point(outlet_lon, outlet_lat)], crs="EPSG:4326").to_crs(metric_crs)[0]
    nearest_index = metric.geometry.distance(outlet).idxmin()
    snapped_outlet = nearest_points(outlet, metric.loc[nearest_index].geometry)[1]
    rows.append(
        {
            "candidate_id": "watershed-outlet",
            "reason": "watershed_outlet",
            "node_id": str(metric.loc[nearest_index, to_node]),
            "n_in": 1,
            "review_status": "required",
        }
    )
    geometries.append(snapped_outlet)

    candidates = gpd.GeoDataFrame(rows, geometry=geometries, crs=metric_crs).to_crs(flowlines.crs)
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_file(target, layer="pour_point_candidates", driver="GPKG")
    return target

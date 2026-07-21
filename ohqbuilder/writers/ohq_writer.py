from __future__ import annotations

import math
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from ..model.watershed import Watershed
from .block_writer import BlockWriter
from .rainfall_writer import rainfall_lines
from .routing_writer import default_pipe_properties, sewer_pipe_properties


def _safe_name(value: Any, fallback: str) -> str:
    text = str(value or fallback)
    text = text.replace(";", "_").replace(",", "_")
    text = " ".join(text.split())
    return text or fallback


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _positive(value: Any, default: float) -> float:
    number = _finite(value, default)
    return number if number > 0.0 else default


def _resource_path(filename: str) -> str:
    root = os.environ.get(
        "OPENHYDROQUAL_RESOURCES",
        "/mnt/3rd900/Projects/OpenHydroQual/resources",
    )
    return str(Path(root) / filename)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _layout_levels(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
    outlet_name: str,
) -> dict[str, int]:
    """Assign left-to-right topological levels to routing blocks."""

    node_list = _ordered_unique(nodes)
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {name: 0 for name in node_list}

    for source, target in edges:
        if source not in indegree or target not in indegree or source == target:
            continue
        if target not in outgoing[source]:
            outgoing[source].append(target)
            indegree[target] += 1

    queue = deque(name for name in node_list if indegree[name] == 0)
    level = {name: 0 for name in node_list}
    visited: set[str] = set()

    while queue:
        source = queue.popleft()
        visited.add(source)
        for target in outgoing.get(source, []):
            level[target] = max(level[target], level[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    # Keep cyclic or disconnected objects visible instead of failing generation.
    for name in node_list:
        if name not in visited:
            level[name] = max(level.values(), default=0) + 1

    # The outlet must always be the right-most routing block.
    if outlet_name in level:
        level[outlet_name] = max(level.values(), default=0) + 1

    return level


class OHQWriter:
    """Write a native OpenHydroQual command-script watershed model.

    Representation
    --------------
    * GIS subbasins become ``Catchment`` blocks.
    * GIS junctions become ``Catch basin`` routing blocks.
    * GIS reaches become ``Sewer_pipe`` links.
    * A synthetic inlet block is created only for a headwater reach that has no
      upstream junction.
    * The watershed outlet becomes one ``fixed_head`` block.

    The writer collapses the former reach-as-block graph.  It follows explicit
    topology only and never force-connects unresolved objects to the outlet.
    """

    def __init__(self, include_comments: bool = True):
        self.include_comments = include_comments

    def write(self, watershed: Watershed, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(watershed), encoding="utf-8")

    def render(self, watershed: Watershed) -> str:
        writer = BlockWriter()

        model_name = _safe_name(getattr(watershed, "name", None), "Watershed")
        outlet_obj = getattr(watershed, "outlet", None)
        outlet_name = _safe_name(
            getattr(outlet_obj, "name", None),
            f"{model_name} Outlet",
        )

        subbasins = list(getattr(watershed, "subbasins", []) or [])
        reaches = list(getattr(watershed, "reaches", []) or [])
        junctions = list(getattr(watershed, "junctions", []) or [])
        topology = list(getattr(watershed, "topology", []) or [])

        subbasin_by_name = {
            _safe_name(getattr(item, "name", None), f"Subbasin {index + 1}"): item
            for index, item in enumerate(subbasins)
        }
        reach_by_name = {
            _safe_name(getattr(item, "name", None), f"Reach {index + 1}"): item
            for index, item in enumerate(reaches)
        }
        junction_by_name = {
            _safe_name(getattr(item, "name", None), f"Junction {index + 1}"): item
            for index, item in enumerate(junctions)
        }

        subbasin_names = set(subbasin_by_name)
        reach_names = set(reach_by_name)
        junction_names = set(junction_by_name)
        known_names = subbasin_names | reach_names | junction_names | {outlet_name}

        # Normalize and validate the explicit topology once.
        topology_rows: list[tuple[str, str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        skipped_comments: list[str] = []

        for index, link in enumerate(topology):
            source = _safe_name(
                getattr(link, "name", None),
                f"Topology source {index + 1}",
            )
            target_raw = getattr(link, "ds_name", None)
            target = _safe_name(target_raw, "") if target_raw else ""
            link_name = _safe_name(
                getattr(link, "link_name", None),
                f"{source} to {target}",
            )

            if not source or not target:
                skipped_comments.append(
                    f"Skipped topology row {index + 1}: blank source or downstream."
                )
                continue
            if source == target:
                skipped_comments.append(f"Skipped invalid self-link: {source} -> {target}")
                continue
            if source not in known_names or target not in known_names:
                skipped_comments.append(
                    f"Skipped unresolved topology link: {source} -> {target}"
                )
                continue
            if (source, target) in seen_pairs:
                continue

            seen_pairs.add((source, target))
            topology_rows.append((source, target, link_name))

        downstream: dict[str, list[str]] = defaultdict(list)
        upstream: dict[str, list[str]] = defaultdict(list)
        link_name_by_pair: dict[tuple[str, str], str] = {}
        for source, target, link_name in topology_rows:
            downstream[source].append(target)
            upstream[target].append(source)
            link_name_by_pair[(source, target)] = link_name

        def first_downstream_routing(start: str) -> str | None:
            """Follow reach chains to the first junction or outlet."""

            queue = deque(downstream.get(start, []))
            visited = {start}
            while queue:
                name = queue.popleft()
                if name in visited:
                    continue
                visited.add(name)
                if name in junction_names or name == outlet_name:
                    return name
                if name in reach_names:
                    queue.extend(downstream.get(name, []))
            return None

        def first_upstream_junction(start: str) -> str | None:
            """Walk upstream through reach chains to the nearest junction."""

            queue = deque(upstream.get(start, []))
            visited = {start}
            while queue:
                name = queue.popleft()
                if name in visited:
                    continue
                visited.add(name)
                if name in junction_names:
                    return name
                if name in reach_names:
                    queue.extend(upstream.get(name, []))
            return None

        # Only reaches that actually participate in explicit topology are emitted.
        active_reaches = [
            name
            for name in reach_by_name
            if name in downstream or name in upstream
        ]

        reach_endpoints: dict[str, tuple[str, str]] = {}
        synthetic_inlets: dict[str, str] = {}

        for reach_name in active_reaches:
            target = first_downstream_routing(reach_name)
            if target is None:
                skipped_comments.append(
                    f"Skipped reach without downstream routing target: {reach_name}"
                )
                continue

            source = first_upstream_junction(reach_name)
            if source is None:
                source = _safe_name(f"{reach_name} Inlet", f"{reach_name} Inlet")
                synthetic_inlets[reach_name] = source

            if source == target:
                skipped_comments.append(
                    f"Skipped collapsed self-link for reach: {reach_name} ({source})"
                )
                continue

            reach_endpoints[reach_name] = (source, target)

        # Direct routing links are topology rows not already represented by a reach.
        direct_routing_edges: list[tuple[str, str, str]] = []
        for source, target, link_name in topology_rows:
            if source in junction_names and (target in junction_names or target == outlet_name):
                direct_routing_edges.append((source, target, link_name))

        routing_edges = list(reach_endpoints.values()) + [
            (source, target) for source, target, _ in direct_routing_edges
        ]

        # Emit only routing blocks that actually participate in the generated
        # network.  Previously every GIS junction was created even when it had
        # no emitted incoming or outgoing link, leaving isolated Catch basin
        # blocks in the OHQ canvas.
        connected_routing_nodes: set[str] = {outlet_name}
        for source, target in routing_edges:
            connected_routing_nodes.add(source)
            connected_routing_nodes.add(target)

        routing_nodes = [
            name
            for name in [
                *junction_by_name.keys(),
                *synthetic_inlets.values(),
                outlet_name,
            ]
            if name in connected_routing_nodes
        ]
        levels = _layout_levels(routing_nodes, routing_edges, outlet_name)

        # Arrange routing blocks as a compact left-to-right drainage tree.
        # Nodes within each level are ordered by their upstream catchments and
        # predecessor positions. This substantially reduces crossed links.
        nodes_by_level: dict[int, list[str]] = defaultdict(list)
        for name in routing_nodes:
            nodes_by_level[levels.get(name, 0)].append(name)

        subbasin_order = {
            name: index for index, name in enumerate(subbasin_by_name.keys())
        }

        def upstream_catchment_rank(start: str) -> float:
            queue = deque([start])
            visited: set[str] = set()
            ranks: list[int] = []
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                for parent in upstream.get(current, []):
                    if parent in subbasin_order:
                        ranks.append(subbasin_order[parent])
                    elif parent in reach_names or parent in junction_names:
                        queue.append(parent)
            if ranks:
                return sum(ranks) / len(ranks)
            return float("inf")

        routing_predecessors: dict[str, list[str]] = defaultdict(list)
        for source, target in routing_edges:
            routing_predecessors[target].append(source)

        routing_positions: dict[str, tuple[int, int]] = {}
        routing_order: dict[str, float] = {}

        # Compact defaults keep the complete model readable at normal zoom.
        # They remain configurable for unusually large networks.
        x_spacing = int(os.environ.get("OHQ_LAYOUT_X_SPACING", "460"))
        y_spacing = int(os.environ.get("OHQ_LAYOUT_Y_SPACING", "260"))

        for level_value in sorted(nodes_by_level):
            names = nodes_by_level[level_value]

            def ordering_key(name: str) -> tuple[float, float, str]:
                predecessors = [
                    routing_order[parent]
                    for parent in routing_predecessors.get(name, [])
                    if parent in routing_order
                ]
                predecessor_rank = (
                    sum(predecessors) / len(predecessors)
                    if predecessors
                    else upstream_catchment_rank(name)
                )
                return (
                    predecessor_rank,
                    upstream_catchment_rank(name),
                    name,
                )

            names.sort(key=ordering_key)
            center_offset = (len(names) - 1) * y_spacing / 2.0
            for index, name in enumerate(names):
                order_value = float(index)
                routing_order[name] = order_value
                routing_positions[name] = (
                    level_value * x_spacing,
                    int(index * y_spacing - center_offset),
                )

        # Resolve each catchment to a routing block. A catchment assigned to a
        # reach drains to that reach's upstream endpoint.
        catchment_targets: dict[str, str] = {}
        for subbasin_name in subbasin_by_name:
            candidates = downstream.get(subbasin_name, [])
            target: str | None = None
            for candidate in candidates:
                if candidate in junction_names or candidate == outlet_name:
                    target = candidate
                    break
                if candidate in reach_endpoints:
                    target = reach_endpoints[candidate][0]
                    break
            if target is not None:
                catchment_targets[subbasin_name] = target
            else:
                skipped_comments.append(
                    f"Catchment retained without resolved routing target: {subbasin_name}"
                )

        catchments_by_target: dict[str, list[str]] = defaultdict(list)
        for name, target in catchment_targets.items():
            catchments_by_target[target].append(name)
        for names in catchments_by_target.values():
            names.sort()

        catchment_positions: dict[str, tuple[int, int]] = {}
        catchment_x_offset = int(
            os.environ.get("OHQ_LAYOUT_CATCHMENT_X_OFFSET", "430")
        )
        catchment_y_spacing = int(
            os.environ.get("OHQ_LAYOUT_CATCHMENT_Y_SPACING", "220")
        )

        # Keep all catchments in one clean left-hand column, ordered by the
        # vertical position of the routing block they drain to. This produces
        # the conventional watershed schematic: catchments -> local routing
        # nodes -> downstream trunk -> outlet.
        leftmost_routing_x = min(
            (x for x, _ in routing_positions.values()),
            default=0,
        )
        catchment_column_x = leftmost_routing_x - catchment_x_offset

        resolved_catchments = sorted(
            catchment_targets,
            key=lambda name: (
                routing_positions.get(catchment_targets[name], (0, 0))[1],
                name,
            ),
        )
        resolved_center = (
            (len(resolved_catchments) - 1) * catchment_y_spacing / 2.0
        )
        for index, name in enumerate(resolved_catchments):
            catchment_positions[name] = (
                catchment_column_x,
                int(index * catchment_y_spacing - resolved_center),
            )

        # Unresolved catchments are appended below the resolved catchments,
        # still in the same left-hand column.
        unresolved_catchments = sorted(
            name for name in subbasin_by_name if name not in catchment_positions
        )
        unresolved_start_y = int(
            (len(resolved_catchments) + 1) * catchment_y_spacing
            - resolved_center
        )
        for index, name in enumerate(unresolved_catchments):
            catchment_positions[name] = (
                catchment_column_x,
                unresolved_start_y + index * catchment_y_spacing,
            )

        block_width = int(os.environ.get("OHQ_LAYOUT_BLOCK_WIDTH", "300"))
        block_height = int(os.environ.get("OHQ_LAYOUT_BLOCK_HEIGHT", "180"))
        outlet_width = int(os.environ.get("OHQ_LAYOUT_OUTLET_WIDTH", "260"))
        outlet_height = int(os.environ.get("OHQ_LAYOUT_OUTLET_HEIGHT", "160"))

        if self.include_comments:
            writer.comment("Generated by GIStoOHQ using native OpenHydroQual grammar")
            writer.comment("GIS reaches are represented as Sewer_pipe links.")
            writer.comment(
                "Only junctions and necessary headwater inlet nodes are routing blocks."
            )
            writer.comment(
                "Set OPENHYDROQUAL_RESOURCES and OHQ_RAINFALL_FILE when local paths differ."
            )

        writer.loadtemplate(_resource_path("main_components.json"))
        writer.addtemplate(_resource_path("rainfall_runoff.json"))
        writer.addtemplate(_resource_path("Sewer_system.json"))
        writer.line()

        if self.include_comments:
            writer.comment("Meteorological source")
        for line in rainfall_lines(watershed):
            writer.line(line)
        writer.line()

        if self.include_comments:
            writer.comment("Catchment runoff-generation blocks")

        for name, subbasin in subbasin_by_name.items():
            area_km2 = _positive(getattr(subbasin, "area_km2", None), 1.0e-6)
            area_m2 = area_km2 * 1_000_000.0
            slope_pct = _finite(getattr(subbasin, "slope_pct", None), 1.0)
            slope = max(slope_pct / 100.0, 1.0e-6)
            curve_number = _finite(getattr(subbasin, "curve_number", None), 75.0)
            runoff_coeff = min(max(curve_number / 100.0, 0.01), 1.0)
            width = max(math.sqrt(area_m2), 1.0)
            elevation = _finite(
                getattr(subbasin, "elevation_m", None),
                _finite(getattr(subbasin, "mean_elevation_m", None), 0.0),
            )
            x, y = catchment_positions[name]

            writer.create_block(
                "Catchment",
                name=name,
                properties=[
                    ("area", f"{area_m2:.12g}[m~^2]"),
                    ("Slope", f"{slope:.12g}"),
                    ("Width", f"{width:.12g}[m]"),
                    ("ManningCoeff", "0.15"),
                    ("Runoff_coeff", f"{runoff_coeff:.12g}"),
                    ("Precipitation", "Rain"),
                    ("Evapotranspiration", ""),
                    ("inflow", ""),
                    ("depth", "0[m]"),
                    ("elevation", f"{elevation:.12g}[m]"),
                    ("depression_storage", "0.005[m]"),
                    ("loss_coefficient", "0[1/day]"),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ],
            )

        if self.include_comments:
            writer.comment("Junction routing blocks")

        for name, junction in junction_by_name.items():
            if name not in connected_routing_nodes:
                continue
            elevation = _finite(
                getattr(junction, "elevation_m", None),
                _finite(getattr(junction, "z_m", None), 0.0),
            )
            x, y = routing_positions.get(name, (0, 0))
            writer.create_block(
                "Catch basin",
                name=name,
                properties=[
                    ("area", "1[m~^2]"),
                    ("bottom_elevation", f"{elevation:.12g}[m]"),
                    ("inflow", ""),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ],
            )

        if self.include_comments and synthetic_inlets:
            writer.comment("Synthetic headwater inlet blocks")

        for reach_name, inlet_name in synthetic_inlets.items():
            if (
                reach_name not in reach_endpoints
                or inlet_name not in connected_routing_nodes
            ):
                continue
            reach = reach_by_name[reach_name]
            elevation = _finite(getattr(reach, "z_up_m", None), 0.0)
            x, y = routing_positions.get(inlet_name, (0, 0))
            writer.create_block(
                "Catch basin",
                name=inlet_name,
                properties=[
                    ("area", "1[m~^2]"),
                    ("bottom_elevation", f"{elevation:.12g}[m]"),
                    ("inflow", ""),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ],
            )

        outlet_x, outlet_y = routing_positions.get(outlet_name, (1040, 0))
        writer.create_block(
            "fixed_head",
            name=outlet_name,
            properties=[
                ("head", "0[m]"),
                ("Storage", "1000000000[m~^3]"),
                ("x", outlet_x),
                ("y", outlet_y),
                ("_width", outlet_width),
                ("_height", outlet_height),
            ],
        )
        writer.line()

        if self.include_comments:
            writer.comment("Catchment runoff links")

        emitted_links: set[tuple[str, str, str]] = set()
        for source, target in catchment_targets.items():
            key = (source, target, "Catchment_link")
            if key in emitted_links:
                continue
            emitted_links.add(key)
            original_target = downstream.get(source, [target])[0]
            link_name = link_name_by_pair.get(
                (source, original_target),
                f"{source} to {target}",
            )
            writer.create_link(
                "Catchment_link",
                name=link_name,
                source=source,
                target=target,
            )

        if self.include_comments:
            writer.comment("GIS reaches represented as Sewer_pipe links")

        for reach_name, (source, target) in reach_endpoints.items():
            key = (source, target, "Sewer_pipe")
            if key in emitted_links:
                if self.include_comments:
                    writer.comment(
                        f"Skipped duplicate collapsed reach connection: {reach_name}"
                    )
                continue
            emitted_links.add(key)
            writer.create_link(
                "Sewer_pipe",
                name=reach_name,
                source=source,
                target=target,
                properties=sewer_pipe_properties(reach_by_name[reach_name]),
            )

        if self.include_comments and direct_routing_edges:
            writer.comment("Direct junction/outlet routing links")

        for source, target, link_name in direct_routing_edges:
            key = (source, target, "Sewer_pipe")
            if key in emitted_links:
                continue
            emitted_links.add(key)
            writer.create_link(
                "Sewer_pipe",
                name=link_name,
                source=source,
                target=target,
                properties=default_pipe_properties(),
            )

        if self.include_comments:
            writer.comment("Topology diagnostics")
            for comment in skipped_comments:
                writer.comment(comment)
            inactive_reaches = [
                name for name in reach_by_name if name not in active_reaches
            ]
            for name in inactive_reaches:
                writer.comment(
                    f"Skipped GIS reach absent from explicit topology: {name}"
                )
            unused_junctions = [
                name
                for name in junction_by_name
                if name not in connected_routing_nodes
            ]
            for name in unused_junctions:
                writer.comment(
                    f"Skipped isolated GIS junction with no emitted links: {name}"
                )

        writer.line()
        writer.setvalue("system", "simulation_start_time", "0")
        writer.setvalue("system", "simulation_end_time", "1")
        writer.setvalue("system", "outputfile", f"{model_name}_OHQ_output.txt")

        return writer.text()

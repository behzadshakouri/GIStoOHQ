from __future__ import annotations

import math
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from ..model.watershed import Watershed
from .block_writer import BlockWriter
from .rainfall_writer import rainfall_lines
from .routing_writer import trapezoidal_channel_properties


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


def _optional_finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_coordinate(obj: Any, names: Iterable[str]) -> float | None:
    for name in names:
        value = _optional_finite(getattr(obj, name, None))
        if value is not None:
            return value
    return None


def _actual_xy(obj: Any) -> tuple[float | None, float | None]:
    """Read a real projected coordinate pair from a watershed model object."""
    x = _first_coordinate(
        obj,
        (
            "x_act",
            "centroid_x",
            "midpoint_x",
            "mid_x",
            "x_mid",
            "x",
        ),
    )
    y = _first_coordinate(
        obj,
        (
            "y_act",
            "centroid_y",
            "midpoint_y",
            "mid_y",
            "y_mid",
            "y",
        ),
    )
    return x, y


def _reach_actual_xy(reach: Any) -> tuple[float | None, float | None]:
    x, y = _actual_xy(reach)
    if x is not None and y is not None:
        return x, y

    x_up = _first_coordinate(reach, ("x_up_act", "x_up", "up_x"))
    y_up = _first_coordinate(reach, ("y_up_act", "y_up", "up_y"))
    x_dn = _first_coordinate(reach, ("x_dn_act", "x_dn", "down_x"))
    y_dn = _first_coordinate(reach, ("y_dn_act", "y_dn", "down_y"))
    if None not in (x_up, y_up, x_dn, y_dn):
        return 0.5 * (x_up + x_dn), 0.5 * (y_up + y_dn)
    return None, None


def _reach_downstream_xy(reach: Any) -> tuple[float | None, float | None]:
    x = _first_coordinate(reach, ("x_dn_act", "x_dn", "down_x"))
    y = _first_coordinate(reach, ("y_dn_act", "y_dn", "down_y"))
    if x is not None and y is not None:
        return x, y
    return _reach_actual_xy(reach)


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
) -> dict[str, int]:
    """Assign left-to-right levels to stream reaches."""

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
    levels = {name: 0 for name in node_list}
    visited: set[str] = set()

    while queue:
        source = queue.popleft()
        visited.add(source)
        for target in outgoing.get(source, []):
            levels[target] = max(levels[target], levels[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    # Preserve cyclic/disconnected reaches on the canvas for diagnostics.
    fallback_level = max(levels.values(), default=0) + 1
    for name in node_list:
        if name not in visited:
            levels[name] = fallback_level
            fallback_level += 1

    return levels


class OHQWriter:
    """Write a native OpenHydroQual watershed/open-channel model.

    Representation
    --------------
    * GIS subbasins become ``Catchment`` blocks.
    * GIS reaches become ``Trapezoidal Channel Segment`` blocks.
    * Catchments discharge directly to their first downstream reach using
      ``Catchment_link``.
    * Consecutive reaches are connected by ``Trapezoidal_Channel_link``.
    * Terminal reaches discharge to one ``fixed_head`` outlet through
      ``channel2fixed``.
    * GIS junctions are treated as topology nodes and are not emitted as
      artificial storage blocks.

    This representation preserves the physical sequence:

        watershed -> stream reach -> downstream stream reach -> outlet
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

        topology_rows: list[tuple[str, str, str]] = []
        skipped_comments: list[str] = []
        seen_pairs: set[tuple[str, str]] = set()

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

        def first_downstream_reach(start: str) -> str | None:
            """Traverse junctions until the first downstream stream reach."""

            queue = deque(downstream.get(start, []))
            visited = {start}

            while queue:
                name = queue.popleft()
                if name in visited:
                    continue
                visited.add(name)

                if name in reach_names:
                    return name
                if name in junction_names:
                    queue.extend(downstream.get(name, []))

            return None

        def downstream_reach_or_outlet(start_reach: str) -> str | None:
            """Find the next reach, or the model outlet, after one reach."""

            queue = deque(downstream.get(start_reach, []))
            visited = {start_reach}

            while queue:
                name = queue.popleft()
                if name in visited:
                    continue
                visited.add(name)

                if name in reach_names or name == outlet_name:
                    return name
                if name in junction_names:
                    queue.extend(downstream.get(name, []))

            return None

        # A reach is active when it occurs in explicit topology or receives a
        # catchment that resolves to it.
        active_reach_names: set[str] = {
            name
            for name in reach_names
            if name in downstream or name in upstream
        }

        catchment_targets: dict[str, str] = {}
        for subbasin_name in subbasin_by_name:
            target = first_downstream_reach(subbasin_name)
            if target is None:
                skipped_comments.append(
                    f"Catchment retained without downstream stream reach: {subbasin_name}"
                )
                continue
            catchment_targets[subbasin_name] = target
            active_reach_names.add(target)

        # Resolve each active reach to the next reach or to the outlet.
        reach_targets: dict[str, str] = {}
        for reach_name in reach_by_name:
            if reach_name not in active_reach_names:
                continue
            target = downstream_reach_or_outlet(reach_name)
            if target is None:
                skipped_comments.append(
                    f"Stream reach retained without downstream reach/outlet: {reach_name}"
                )
                continue
            if target == reach_name:
                skipped_comments.append(
                    f"Skipped collapsed self-link for stream reach: {reach_name}"
                )
                continue
            reach_targets[reach_name] = target
            if target in reach_names:
                active_reach_names.add(target)

        # Include reaches discovered as downstream targets, then resolve them too.
        pending = deque(
            name for name in active_reach_names if name not in reach_targets
        )
        processed: set[str] = set()

        while pending:
            reach_name = pending.popleft()
            if reach_name in processed:
                continue
            processed.add(reach_name)

            target = downstream_reach_or_outlet(reach_name)
            if target is None or target == reach_name:
                continue
            reach_targets[reach_name] = target
            if target in reach_names and target not in active_reach_names:
                active_reach_names.add(target)
                pending.append(target)

        channel_edges = [
            (source, target)
            for source, target in reach_targets.items()
            if target in reach_names
        ]

        active_reaches = [
            name for name in reach_by_name if name in active_reach_names
        ]

        # Real projected GIS coordinates are authoritative. No synthetic
        # topological grid is generated here.
        catchment_positions: dict[str, tuple[float, float]] = {}
        missing_coordinates: list[str] = []

        for name, subbasin in subbasin_by_name.items():
            x_act, y_act = _actual_xy(subbasin)
            if x_act is None or y_act is None:
                missing_coordinates.append(
                    f"{name}: missing x_act/y_act or centroid_x/centroid_y"
                )
            else:
                catchment_positions[name] = (x_act, y_act)

        reach_positions: dict[str, tuple[float, float]] = {}
        for name in active_reaches:
            x_act, y_act = _reach_actual_xy(reach_by_name[name])
            if x_act is None or y_act is None:
                missing_coordinates.append(
                    f"{name}: missing reach midpoint x_act/y_act and endpoints"
                )
            else:
                reach_positions[name] = (x_act, y_act)

        terminal_reaches = [
            source
            for source, target in reach_targets.items()
            if target == outlet_name
        ]

        outlet_x, outlet_y = _actual_xy(outlet_obj)
        if outlet_x is None or outlet_y is None:
            terminal_points = [
                _reach_downstream_xy(reach_by_name[name])
                for name in terminal_reaches
                if name in reach_by_name
            ]
            terminal_points = [
                point
                for point in terminal_points
                if point[0] is not None and point[1] is not None
            ]
            if terminal_points:
                outlet_x = sum(point[0] for point in terminal_points) / len(terminal_points)
                outlet_y = sum(point[1] for point in terminal_points) / len(terminal_points)

        if outlet_x is None or outlet_y is None:
            missing_coordinates.append(
                f"{outlet_name}: missing outlet x_act/y_act and terminal reach endpoint"
            )

        if missing_coordinates:
            raise ValueError(
                "Cannot write a spatially referenced OHQ model. "
                "All emitted blocks require real projected coordinates:\n  "
                + "\n  ".join(missing_coordinates)
            )

        block_width = int(os.environ.get("OHQ_LAYOUT_BLOCK_WIDTH", "320"))
        block_height = int(os.environ.get("OHQ_LAYOUT_BLOCK_HEIGHT", "190"))
        outlet_width = int(os.environ.get("OHQ_LAYOUT_OUTLET_WIDTH", "260"))
        outlet_height = int(os.environ.get("OHQ_LAYOUT_OUTLET_HEIGHT", "160"))

        if self.include_comments:
            writer.comment("Generated by GIStoOHQ using native OpenHydroQual grammar")
            writer.comment(
                "GIS reaches are Trapezoidal Channel Segment blocks from open_channel.json."
            )
            writer.comment(
                "Catchments discharge to stream reaches; GIS junctions are topology-only."
            )
            writer.comment(
                "Block x/y values are real projected GIS coordinates, not schematic indices."
            )
            writer.comment(
                "Set OPENHYDROQUAL_RESOURCES and OHQ_RAINFALL_FILE when local paths differ."
            )

        writer.loadtemplate(_resource_path("main_components.json"))
        writer.addtemplate(_resource_path("rainfall_runoff.json"))
        writer.addtemplate(_resource_path("open_channel.json"))
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

        writer.line()

        if self.include_comments:
            writer.comment("Trapezoidal stream-reach blocks")

        for name in active_reaches:
            reach = reach_by_name[name]
            x, y = reach_positions.get(name, (0, 0))
            writer.create_block(
                "Trapezoidal Channel Segment",
                name=name,
                properties=[
                    *trapezoidal_channel_properties(reach),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ],
            )

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

        emitted_links: set[tuple[str, str, str]] = set()

        if self.include_comments:
            writer.comment("Watershed runoff discharging into stream reaches")

        for source, target in catchment_targets.items():
            key = (source, target, "Catchment_link")
            if key in emitted_links:
                continue
            emitted_links.add(key)

            first_step = downstream.get(source, [target])[0]
            link_name = link_name_by_pair.get(
                (source, first_step),
                f"{source} to {target}",
            )
            writer.create_link(
                "Catchment_link",
                name=link_name,
                source=source,
                target=target,
            )

        if self.include_comments:
            writer.comment("Stream-reach routing links")

        for source, target in reach_targets.items():
            if target in reach_names:
                link_type = "Trapezoidal_Channel_link"
            elif target == outlet_name:
                link_type = "channel2fixed"
            else:
                continue

            key = (source, target, link_type)
            if key in emitted_links:
                continue
            emitted_links.add(key)

            direct_step = downstream.get(source, [target])[0]
            link_name = link_name_by_pair.get(
                (source, direct_step),
                f"{source} to {target}",
            )
            writer.create_link(
                link_type,
                name=link_name,
                source=source,
                target=target,
            )

        if self.include_comments:
            writer.comment("Topology diagnostics")
            for comment in skipped_comments:
                writer.comment(comment)

            for name in reach_by_name:
                if name not in active_reach_names:
                    writer.comment(
                        f"Skipped GIS reach absent from resolved stream topology: {name}"
                    )

            for name in junction_by_name:
                writer.comment(
                    f"GIS junction used as topology-only node: {name}"
                )

        if self.include_comments:
            crs_values = _ordered_unique(
                str(getattr(item, "crs_authid", "") or "")
                for item in [*subbasins, *reaches, *junctions]
            )
            if crs_values:
                writer.comment("Projected coordinate reference: " + ", ".join(crs_values))

        writer.line()
        writer.setvalue("system", "simulation_start_time", "0")
        writer.setvalue("system", "simulation_end_time", "1")
        writer.setvalue("system", "outputfile", f"{model_name}_OHQ_output.txt")

        return writer.text()

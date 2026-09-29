from __future__ import annotations

import math
import os
from dataclasses import replace
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from ..model.watershed import Watershed
from ..soil_pedotransfer import van_genuchten_params
from ..channel_sections import load_channel_segments, position_on_reach, segment_at_station, station_on_reach
from .block_writer import BlockWriter
from .rainfall_writer import rainfall_lines
from .et_writer import et_filename, et_lines
from .snow_writer import snow_forcing_lines, temperature_filename
from .routing_writer import reach_bottom_elevation, trapezoidal_channel_properties


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


def _scale_layout_positions(
    catchment_positions: dict[str, tuple[float, float]],
    reach_positions: dict[str, tuple[float, float]],
    outlet_position: tuple[float, float],
) -> tuple[
    dict[str, tuple[float, float]],
    dict[str, tuple[float, float]],
    tuple[float, float],
    dict[str, float],
]:
    """Convert projected GIS coordinates to a compact OHQ canvas layout.

    The transformation preserves relative geometry and aspect ratio while
    translating the model to a local origin and fitting it inside a configurable
    display canvas. Authoritative GIS coordinates remain stored on the model
    objects and are only transformed for the emitted OHQ block ``x``/``y``
    properties.

    Environment variables
    ---------------------
    OHQ_LAYOUT_TARGET_WIDTH
        Maximum drawable width in OHQ canvas units. Default: 5000.
    OHQ_LAYOUT_TARGET_HEIGHT
        Maximum drawable height in OHQ canvas units. Default: 5000.
    OHQ_LAYOUT_MARGIN
        Margin around the fitted model. Default: 500.
    OHQ_LAYOUT_FLIP_Y
        Controls only the emitted OHQ canvas view. The default is true, so
        north/up in GIS appears upward in the OHQ viewer. Set to 0/false/no/off
        to retain the unflipped canvas direction.
    OHQ_LAYOUT_MAX_SCALE
        Optional upper limit on enlargement. Default: 1.0. This prevents small
        watersheds from being unnecessarily magnified.
    """

    all_points = [
        *catchment_positions.values(),
        *reach_positions.values(),
        outlet_position,
    ]
    if not all_points:
        return (
            dict(catchment_positions),
            dict(reach_positions),
            outlet_position,
            {
                "scale": 1.0,
                "source_width": 0.0,
                "source_height": 0.0,
                "target_width": 0.0,
                "target_height": 0.0,
                "margin": 0.0,
            },
        )

    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]

    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)

    source_width = max_x - min_x
    source_height = max_y - min_y

    target_width = _positive(
        os.environ.get("OHQ_LAYOUT_TARGET_WIDTH", "5000"),
        5000.0,
    )
    target_height = _positive(
        os.environ.get("OHQ_LAYOUT_TARGET_HEIGHT", "5000"),
        5000.0,
    )
    margin = max(
        _finite(os.environ.get("OHQ_LAYOUT_MARGIN", "500"), 500.0),
        0.0,
    )
    max_scale = _positive(
        os.environ.get("OHQ_LAYOUT_MAX_SCALE", "1.0"),
        1.0,
    )

    available_width = max(target_width - 2.0 * margin, 1.0)
    available_height = max(target_height - 2.0 * margin, 1.0)

    width_scale = available_width / source_width if source_width > 0.0 else float("inf")
    height_scale = (
        available_height / source_height if source_height > 0.0 else float("inf")
    )
    scale = min(width_scale, height_scale, max_scale)
    if not math.isfinite(scale) or scale <= 0.0:
        scale = 1.0

    # This affects only the OHQ display coordinates. It does not modify
    # x_act/y_act or any authoritative GIS coordinate stored in the model.
    flip_y = str(os.environ.get("OHQ_LAYOUT_FLIP_Y", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    fitted_width = source_width * scale
    fitted_height = source_height * scale

    # Center the fitted watershed inside the configured canvas.
    offset_x = margin + max((available_width - fitted_width) / 2.0, 0.0)
    offset_y = margin + max((available_height - fitted_height) / 2.0, 0.0)

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        canvas_x = offset_x + (x - min_x) * scale
        if flip_y:
            canvas_y = offset_y + (max_y - y) * scale
        else:
            canvas_y = offset_y + (y - min_y) * scale
        return canvas_x, canvas_y

    return (
        {name: transform(point) for name, point in catchment_positions.items()},
        {name: transform(point) for name, point in reach_positions.items()},
        transform(outlet_position),
        {
            "scale": scale,
            "source_width": source_width,
            "source_height": source_height,
            "target_width": target_width,
            "target_height": target_height,
            "margin": margin,
        },
    )


def _resource_path(filename: str) -> str:
    root = os.environ.get(
        "OPENHYDROQUAL_RESOURCES",
        "/mnt/3rd900/Projects/OpenHydroQual/resources",
    )
    return str(Path(root) / filename)


def _simulation_window() -> tuple[str, str]:
    """Return (start, end) simulation time, in the model's day-based time unit.

    Defaults to the original 0/1 placeholder window (kept for backward
    compatibility with any script relying on it). Set OHQ_SIM_START_TIME /
    OHQ_SIM_END_TIME to drive a real run -- e.g. to match a rainfall
    timeseries built for a specific date range via OHQ_RAINFALL_FILE.
    """

    start = os.environ.get("OHQ_SIM_START_TIME", "0").strip() or "0"
    end = os.environ.get("OHQ_SIM_END_TIME", "1").strip() or "1"
    try:
        start_value = float(start)
        end_value = float(end)
    except ValueError as exc:
        raise ValueError(
            "OHQ_SIM_START_TIME/OHQ_SIM_END_TIME must be numbers "
            f"(got start={start!r}, end={end!r})"
        ) from exc
    if not (math.isfinite(start_value) and math.isfinite(end_value)):
        raise ValueError(
            "OHQ_SIM_START_TIME/OHQ_SIM_END_TIME must be finite "
            f"(got start={start!r}, end={end!r})"
        )
    if end_value <= start_value:
        raise ValueError(
            "OHQ_SIM_END_TIME must be greater than OHQ_SIM_START_TIME "
            f"(got start={start!r}, end={end!r})"
        )
    return start, end


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
    * GIS subbasins become ``CN_Catchment`` composites (formulation="legacy")
      or ``Mixed_Hydrologic_Response_Unit`` composites (formulation="mixed_hru")
      or ``Hydrologic_Response_Unit`` composites (formulation="standard_hru").
      Both are composite types and are emitted with ``create composite``, not
      ``create block`` - the latter never instantiates a composite's internal
      members.
    * GIS reaches become ``Trapezoidal Channel Segment`` blocks.
    * Catchments discharge to their first downstream reach using
      ``CN_outlet`` (legacy), reach ports (mixed_hru), or
      ``Catchment_link`` (standard_hru). Both HRU types also route groundwater
      to the reach with ``groundwater_to_stream``.
    * Consecutive reaches are connected by ``Trapezoidal_Channel_link``.
    * Terminal reaches discharge to one ``fixed_head`` outlet through
      ``channel2fixed``.
    * GIS junctions are treated as topology nodes and are not emitted as
      artificial storage blocks.

    This representation preserves the physical sequence:

        watershed -> stream reach -> downstream stream reach -> outlet
    """

    def __init__(self, include_comments: bool = True, formulation: str = "legacy", channel_profiles_path: Path | None = None):
        if formulation not in {"legacy", "mixed_hru", "standard_hru"}:
            raise ValueError(f"Unsupported OHQ formulation: {formulation}")
        self.include_comments = include_comments
        self.formulation = formulation
        self.channel_profiles_path = channel_profiles_path

    def write(self, watershed: Watershed, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(watershed, output_name=path.stem), encoding="utf-8")

    def render(self, watershed: Watershed, *, output_name: str | None = None) -> str:
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
        channel_segments = (
            load_channel_segments(self.channel_profiles_path, reach_by_name)
            if self.channel_profiles_path else {}
        )
        if channel_segments and self.formulation != "mixed_hru":
            raise ValueError("Detailed channel segmentation currently supports mixed_hru only")
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
                skipped_comments.append(
                    f"Skipped invalid self-link: {source} -> {target}"
                )
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
            name for name in reach_names if name in downstream or name in upstream
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

        active_reaches = [name for name in reach_by_name if name in active_reach_names]

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
            if name in channel_segments:
                for section in channel_segments[name]:
                    midpoint = (section.start_m + section.end_m) / (2 * reach_by_name[name].length_m)
                    position = position_on_reach(reach_by_name[name], midpoint)
                    if position is None:
                        missing_coordinates.append(f"{section.name}: missing centerline position")
                    else:
                        reach_positions[section.name] = position

        terminal_reaches = [
            source for source, target in reach_targets.items() if target == outlet_name
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
                outlet_x = sum(point[0] for point in terminal_points) / len(
                    terminal_points
                )
                outlet_y = sum(point[1] for point in terminal_points) / len(
                    terminal_points
                )

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

        # Preserve the real GIS geometry, but fit the emitted OHQ block
        # coordinates into a compact canvas so blocks remain legible when the
        # watershed covers a large projected extent.
        (
            catchment_positions,
            reach_positions,
            (outlet_x, outlet_y),
            layout_info,
        ) = _scale_layout_positions(
            catchment_positions,
            reach_positions,
            (outlet_x, outlet_y),
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
                "Block x/y values preserve GIS geometry after uniform canvas scaling."
            )
            writer.comment(
                "The OHQ display Y direction is flipped by default; actual GIS Y values are unchanged."
            )
            writer.comment(
                "Authoritative projected coordinates remain on the watershed model objects."
            )
            writer.comment(
                "Layout scale={scale:.12g}; source span={width:.12g} x {height:.12g}; "
                "target canvas={target_width:.12g} x {target_height:.12g}; margin={margin:.12g}.".format(
                    scale=layout_info["scale"],
                    width=layout_info["source_width"],
                    height=layout_info["source_height"],
                    target_width=layout_info["target_width"],
                    target_height=layout_info["target_height"],
                    margin=layout_info["margin"],
                )
            )
            writer.comment(
                "Set OPENHYDROQUAL_RESOURCES, OHQ_RAINFALL_FILE and OHQ_ET_FILE as needed."
            )

        writer.loadtemplate(_resource_path("main_components.json"))
        writer.addtemplate(_resource_path("rainfall_runoff.json"))
        writer.addtemplate(_resource_path("open_channel.json"))
        forcing_snow = temperature_filename()
        if forcing_snow:
            writer.addtemplate(_resource_path("snowmelt.json"))
        if self.formulation == "mixed_hru":
            writer.addtemplate(_resource_path("mixed_hydrologic_response_unit.json"))
        elif self.formulation == "standard_hru":
            writer.addtemplate(_resource_path("hydrologic_response_unit.json"))
        else:
            writer.addtemplate(_resource_path("cn_catchment.json"))
        forcing_et = et_filename() if self.formulation != "legacy" else None
        if forcing_et:
            writer.addtemplate(_resource_path("soil_evapotranspiration_models.json"))
        writer.line()

        if self.include_comments:
            writer.comment("Meteorological source")
        forcing_lines = (
            snow_forcing_lines(watershed, forcing_snow)
            if forcing_snow
            else rainfall_lines(watershed)
        )
        for line in forcing_lines:
            writer.line(line)
        if forcing_et:
            for line in et_lines(forcing_et):
                writer.line(line)
        writer.line()

        if self.include_comments:
            writer.comment("Catchment runoff-generation blocks")

        for name, subbasin in subbasin_by_name.items():
            area_km2 = _positive(getattr(subbasin, "area_km2", None), 1.0e-6)
            area_m2 = area_km2 * 1_000_000.0
            slope_pct = _finite(getattr(subbasin, "slope_pct", None), 1.0)
            slope = max(slope_pct / 100.0, 1.0e-6)
            # Curve Number is a retention parameter for the SCS-CN nonlinear
            # abstraction formula (S=25.4/CN-0.254 m, Ia=0.2S), not a linear
            # rainfall multiplier: CN_Catchment consumes it directly and
            # applies that formula internally, so it is passed through here
            # unconverted rather than divided by 100 into a fake Runoff_coeff.
            curve_number = min(max(_finite(getattr(subbasin, "curve_number", None), 75.0), 30.0), 98.0)
            # NRCS lag needs the longest flow path length; GIStoOHQ's legacy
            # GIS scripts already compute this per subbasin (flow_len_ft, from
            # longest_flow_paths.gpkg) and it is already read onto Subbasin by
            # subbasin_reader.py, just unused by this writer until now.
            flow_len_ft = _optional_finite(getattr(subbasin, "flow_len_ft", None))
            hydraulic_length = (
                flow_len_ft * 0.3048
                if flow_len_ft is not None and flow_len_ft > 0
                else None
            )
            impervious_fraction = _finite(
                getattr(subbasin, "impervious_fraction", None), 0.2
            )
            # The mixed composite deliberately excludes zero-area members.
            impervious_fraction = min(max(impervious_fraction, 1.0e-6), 1.0 - 1.0e-6)
            width = max(math.sqrt(area_m2), 1.0)
            # The mixed HRU's internal impervious reach needs a travel length.
            # Prefer the GIS-derived longest flow path already carried by the
            # subbasin.  Fall back to the characteristic basin dimension when
            # older inputs do not provide that field, and retain the template's
            # positive 10 m nominal minimum for very small synthetic fixtures.
            impervious_reach_length = max(hydraulic_length or width, 10.0)
            elevation = _finite(
                getattr(subbasin, "surface_elevation_m", None),
                _finite(
                    getattr(subbasin, "elevation_m", None),
                    _finite(getattr(subbasin, "mean_elevation_m", None), 0.0),
                ),
            )
            x, y = catchment_positions[name]

            # SSURGO-derived sand/clay percentages, when available from the
            # GIS pipeline (extract_soil_texture.py), are converted into
            # site-specific van Genuchten parameters via the Carsel & Parrish
            # (1988) textural-class lookup. Sites without texture data (e.g.
            # Sligo Creek today) fall back to mixed_hydrologic_response_unit.json's
            # generic template defaults by simply omitting these properties.
            sand_pct = _optional_finite(getattr(subbasin, "sand_pct", None))
            clay_pct = _optional_finite(getattr(subbasin, "clay_pct", None))
            soil_properties: list[tuple[str, Any]] = []
            if sand_pct is not None and clay_pct is not None:
                vg = van_genuchten_params(sand_pct, clay_pct)
                soil_properties = [
                    ("K_sat", f"{vg.K_sat:.12g}[m/day]"),
                    ("alpha_vG", f"{vg.alpha_vG:.12g}[1/m]"),
                    ("n_vG", f"{vg.n_vG:.12g}"),
                    ("theta_sat", f"{vg.theta_sat:.12g}"),
                    ("theta_res", f"{vg.theta_res:.12g}"),
                ]

            block_type = (
                "Mixed_Hydrologic_Response_Unit"
                if self.formulation == "mixed_hru"
                else "Hydrologic_Response_Unit"
                if self.formulation == "standard_hru"
                else "CN_Catchment"
            )
            properties = (
                [
                    ("area", f"{area_m2:.12g}[m~^2]"),
                    ("impervious_fraction", f"{impervious_fraction:.12g}"),
                    ("catchment_slope", f"{slope:.12g}"),
                    ("catchment_width", f"{width:.12g}[m]"),
                    ("impervious_reach_length", f"{impervious_reach_length:.12g}[m]"),
                    *soil_properties,
                    ("Precipitation", "LiquidRain" if forcing_snow else "Rain"),
                    *([("Evapotranspiration", "ET")] if forcing_et else []),
                    ("surface_elevation", f"{elevation:.12g}[m]"),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ]
                if self.formulation == "mixed_hru"
                else [
                    ("area", f"{area_m2:.12g}[m~^2]"),
                    ("catchment_slope", f"{slope:.12g}"),
                    ("catchment_width", f"{width:.12g}[m]"),
                    ("runoff_coefficient", "1"),
                    *soil_properties,
                    ("Precipitation", "LiquidRain" if forcing_snow else "Rain"),
                    *([("Evapotranspiration", "ET")] if forcing_et else []),
                    ("surface_elevation", f"{elevation:.12g}[m]"),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ]
                if self.formulation == "standard_hru"
                else [
                    ("CN", f"{curve_number:.12g}"),
                    ("area", f"{area_m2:.12g}[m~^2]"),
                    *(
                        [("hydraulic_length", f"{hydraulic_length:.12g}[m]")]
                        if hydraulic_length
                        else []
                    ),
                    ("slope", f"{slope:.12g}"),
                    ("recovery_coefficient", "0.1[1/day]"),
                    ("initial_abstraction_depth", "0[m]"),
                    ("Precipitation", "LiquidRain" if forcing_snow else "Rain"),
                    ("x", x),
                    ("y", y),
                    ("_width", block_width),
                    ("_height", block_height),
                ]
            )
            writer.create_composite(
                block_type,
                name=name,
                properties=properties,
            )

            if forcing_snow:
                snowpacks = (
                    [
                        ("Pervious", area_m2 * (1.0 - impervious_fraction),
                         "Pervious_Snowmelt_link"),
                        ("Impervious", area_m2 * impervious_fraction,
                         "Impervious_Snowmelt_link"),
                    ]
                    if self.formulation == "mixed_hru"
                    else [("", area_m2, "Snowmelt_link")]
                )
                for surface, snow_area, link_type in snowpacks:
                    suffix = f"_{surface}" if surface else ""
                    snow_name = f"Snowpack_{name}{suffix}"
                    writer.create_block(
                        "Snowpack",
                        name=snow_name,
                        properties=[
                            ("area", f"{snow_area:.12g}[m~^2]"),
                            ("Snowfall", "Snowfall"),
                            ("Temperature", "AirTemperature"),
                            ("x", x - 300 if surface != "Impervious" else x + 300),
                            ("y", y - 300),
                            ("_width", 240),
                            ("_height", 180),
                        ],
                    )
                    writer.create_link(
                        link_type,
                        name=f"{snow_name} melt",
                        source=snow_name,
                        target=name,
                    )

        writer.line()

        if self.include_comments:
            writer.comment("Trapezoidal stream-reach blocks")

        emitted_reaches = dict(reach_by_name)
        for name in active_reaches:
            reach = reach_by_name[name]
            sections = channel_segments.get(name)
            if sections:
                z_up = max(reach.z_up_m, reach.z_dn_m)
                z_dn = min(reach.z_up_m, reach.z_dn_m)
                for section in sections:
                    invert = z_up + (z_dn-z_up) * section.end_m/reach.length_m
                    block = replace(reach, length_m=section.end_m-section.start_m,
                                    base_width_m=section.base_width_m,
                                    side_slope_z=section.side_slope_z, z_up_m=invert,
                                    z_dn_m=invert)
                    emitted_reaches[section.name] = block
                    x,y = reach_positions[section.name]
                    writer.create_block("Trapezoidal Channel Segment", name=section.name,
                        properties=[*trapezoidal_channel_properties(block),
                                    ("x",x),("y",y),("_width",block_width),("_height",block_height)])
                    if self.include_comments:
                        writer.comment(f"{section.name}: DEM profile at {section.profile_station_m:g} m; "
                                       f"area-fit RMSE={section.fit_rmse_m2:.4g} m2; bed unverified")
            else:
                x, y = reach_positions.get(name, (0, 0))
                writer.create_block("Trapezoidal Channel Segment", name=name,
                    properties=[*trapezoidal_channel_properties(reach),
                                ("x",x),("y",y),("_width",block_width),("_height",block_height)])

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
            subbasin = subbasin_by_name[source]
            first_step = downstream.get(source, [target])[0]
            link_name = link_name_by_pair.get(
                (source, first_step),
                f"{source} to {target}",
            )
            target_reach = target
            if target in channel_segments:
                outfall = next((link for link in topology if getattr(link, "name", None) == source
                                and getattr(link, "ds_name", None) == target), None)
                station = (station_on_reach(reach_by_name[target], outfall.x_dn_act, outfall.y_dn_act)
                           if outfall is not None and None not in (outfall.x_dn_act, outfall.y_dn_act)
                           else None)
                if station is None:
                    station = reach_by_name[target].length_m
                    if self.include_comments:
                        writer.comment(f"{source}: no mapped outfall on {target}; attached to downstream segment")
                target_reach = segment_at_station(channel_segments[target], station)
            link_types = (
                (
                    # Mixed_Hydrologic_Response_Unit's Reach and Impervious_Reach
                    # members both wrap a plain Trapezoidal Channel Segment, but a
                    # composite can only resolve one external exit per distinct
                    # link "type" string (Composite::ResolvePort keys its ports
                    # map by that exact string - see aquifolium/src/Composite.cpp).
                    # Reach_link/Impervious_Reach_link are therefore real, separate
                    # connector types registered in mixed_hydrologic_response_unit.json
                    # (identical Manning's-equation physics to Trapezoidal_Channel_link,
                    # just distinctly named so each exit resolves), not the shared
                    # Trapezoidal_Channel_link type itself. Verified end-to-end by
                    # actually solving a generated .ohq through OpenHydroQual-Console:
                    # using Trapezoidal_Channel_link here produces "cannot start at
                    # composite" for every subbasin.
                    ("Reach_link", "surface"),
                    ("Impervious_Reach_link", "impervious surface"),
                    ("groundwater_to_stream", "baseflow"),
                )
                if self.formulation == "mixed_hru"
                else (("Catchment_link", "surface"), ("groundwater_to_stream", "baseflow"))
                if self.formulation == "standard_hru"
                # CN_Catchment exposes its outflow through the CN_outlet
                # interface (from its internal final cascade reservoir), not
                # the plain-Catchment Catchment_link connector; see the real
                # OpenHydroQual CN_Catchment example (Examples/CN_Catchment).
                else (("CN_outlet", ""),)
            )
            for link_type, suffix in link_types:
                key = (source, target, f"{link_type}:{suffix}")
                if key in emitted_links:
                    continue
                emitted_links.add(key)
                link_properties: list[tuple[str, Any]] = []
                if link_type == "groundwater_to_stream":
                    # length/area have no template default and a strict >0
                    # criteria (groundwater.json), so an unparameterized link
                    # fails verification before solving. GIStoOHQ has no real
                    # streambed-contact geometry yet, so these remain nominal
                    # placeholders, not measured geometry - but the flow-path
                    # LENGTH can no longer be a bare constant (see below).
                    #
                    # Contact area: same characteristic-width basis as before
                    # (sqrt of subbasin area) times a nominal 10m contact
                    # depth - still just an order-of-magnitude aquifer/stream
                    # interface, unchanged by this fix.
                    catchment_width = max(
                        math.sqrt(
                            _positive(getattr(subbasin, "area_km2", None), 1.0e-6)
                            * 1_000_000.0
                        ),
                        1.0,
                    )
                    # Flow-path length: this connector is a Darcy expression in
                    # groundwater.json, flow = area * hydraulic_conductivity *
                    # (head.s - head.e) / length. head.s (Groundwater) derives
                    # from this composite's own `surface_elevation`, which
                    # GIStoOHQ correctly sets to the SUBBASIN'S MEAN DEM
                    # elevation (right for infiltration/ET). head.e is the
                    # target reach's own real, GIS-derived channel-bottom
                    # elevation, which is the local valley/outlet elevation,
                    # not the subbasin mean. For a large or high-relief
                    # subbasin those two can differ by hundreds of meters. The
                    # previous fixed 10m path turned that relief straight into
                    # the gradient (head_diff/length), so a subbasin with
                    # ~600m of mean-to-channel relief produced a gradient near
                    # 6000% - three orders of magnitude past a real aquifer's
                    # typical 0.1%-5% - and correspondingly unphysical
                    # baseflow. Verified end-to-end on the Zarrineh/Nezamabad
                    # basin (12,400 km^2, subbasins up to 6,064 km^2): Mixed
                    # HRU discharge averaged ~29x the observed record before
                    # this fix, using nothing else but this one connector's
                    # geometry.
                    #
                    # Fix: size the flow path FROM the actual relief so the
                    # resulting gradient is pinned near a plausible regional
                    # value (TARGET_GRADIENT) instead of being whatever falls
                    # out of a constant nobody scaled to basin size. A small,
                    # low-relief subbasin (the Sligo Creek case the original
                    # 10m constant was tuned against) still gets a short path
                    # via the MIN_FLOWPATH_M floor - this degrades back to
                    # essentially the original behavior there, it does not
                    # regress it. A large, high-relief subbasin gets a longer
                    # path that absorbs its relief instead of amplifying it.
                    TARGET_GRADIENT = 0.02  # 2%: mid-range for a regional
                    # unconfined aquifer (realistic range ~0.1%-5%; e.g.
                    # Freeze & Cherry, Groundwater, 1979, table 3.1-style
                    # regional water-table slopes).
                    MIN_FLOWPATH_M = 10.0  # never shorter than the original
                    # nominal path, so a subbasin with negligible relief (or a
                    # reach elevation this data doesn't resolve) behaves
                    # exactly as before.
                    surface_elevation_m = _finite(
                        getattr(subbasin, "surface_elevation_m", None),
                        _finite(
                            getattr(subbasin, "elevation_m", None),
                            _finite(getattr(subbasin, "mean_elevation_m", None), 0.0),
                        ),
                    )
                    reach_elevation_m = reach_bottom_elevation(emitted_reaches.get(target_reach))
                    relief_m = abs(surface_elevation_m - reach_elevation_m)
                    flow_path_length_m = max(relief_m / TARGET_GRADIENT, MIN_FLOWPATH_M)
                    link_properties = [
                        ("length", f"{flow_path_length_m:.12g}[m]"),
                        ("area", f"{catchment_width * 10.0:.12g}[m~^2]"),
                    ]
                writer.create_link(
                    link_type,
                    name=f"{link_name} {suffix}".strip(),
                    source=source,
                    target=target_reach,
                    properties=link_properties,
                )

        if self.include_comments:
            writer.comment("Stream-reach routing links")

        for name, sections in channel_segments.items():
            if name not in active_reach_names:
                continue
            for first, second in zip(sections, sections[1:]):
                writer.create_link("Trapezoidal_Channel_link", name=f"{first.name} to {second.name}",
                                   source=first.name, target=second.name)

        for source, target in reach_targets.items():
            source_block = channel_segments[source][-1].name if source in channel_segments else source
            target_block = channel_segments[target][0].name if target in channel_segments else target
            if target in reach_names:
                link_type = "Trapezoidal_Channel_link"
            elif target == outlet_name:
                link_type = "channel2fixed"
            else:
                continue

            key = (source_block, target_block, link_type)
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
                source=source_block,
                target=target_block,
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
                writer.comment(f"GIS junction used as topology-only node: {name}")

        if self.include_comments:
            crs_values = _ordered_unique(
                str(getattr(item, "crs_authid", "") or "")
                for item in [*subbasins, *reaches, *junctions]
            )
            if crs_values:
                writer.comment(
                    "Projected coordinate reference: " + ", ".join(crs_values)
                )

        writer.line()
        sim_start, sim_end = _simulation_window()
        writer.setvalue("system", "simulation_start_time", sim_start)
        writer.setvalue("system", "simulation_end_time", sim_end)
        # outputfile is the GA optimizer log; alloutputfile controls simulation
        # results. Use the emitted model filename to keep formulations separate.
        result_name = output_name or model_name
        writer.setvalue("system", "alloutputfile", f"{result_name}_OHQ_output.txt")
        writer.setvalue("system", "observed_outputfile", f"{result_name}_observed.txt")

        return writer.text()

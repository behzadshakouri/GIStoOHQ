"""Fit reviewable trapezoidal OHQ segments from DEM cross-section profiles.

The profile CSV follows rasxsprofiles.py with reach_id and reviewed bank offsets
added. A conventional DEM does not resolve submerged channel bathymetry; the
fitted geometry must be checked against survey or independent bankfull data.
"""

from __future__ import annotations

import csv
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChannelSegment:
    name: str
    reach_id: int
    start_m: float
    end_m: float
    base_width_m: float
    side_slope_z: float
    bed_elevation_m: float
    fit_rmse_m2: float
    profile_station_m: float


def _finite(value: str, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"Non-finite {label}: {value!r}")
    return result


def _elevation(samples: list[tuple[float, float]], offset: float) -> float:
    if not samples[0][0] <= offset <= samples[-1][0]:
        raise ValueError("Bank offset lies outside the sampled cross section")
    for (x0, z0), (x1, z1) in zip(samples, samples[1:]):
        if x0 <= offset <= x1:
            return z0 + (z1 - z0) * (offset - x0) / (x1 - x0)
    return samples[-1][1]


def _wetted_area(samples: list[tuple[float, float]], stage: float) -> float:
    """Integrate positive water depth exactly over each linear terrain edge."""
    area = 0.0
    for (x0, z0), (x1, z1) in zip(samples, samples[1:]):
        h0, h1 = stage - z0, stage - z1
        width = x1 - x0
        if h0 >= 0 and h1 >= 0:
            area += width * (h0 + h1) / 2
        elif h0 > 0:
            area += width * h0 * h0 / (2 * (h0 - h1))
        elif h1 > 0:
            area += width * h1 * h1 / (2 * (h1 - h0))
    return area


def fit_trapezoid(
    samples: list[tuple[float, float]], left_bank_m: float, right_bank_m: float
) -> tuple[float, float, float, float]:
    """Return bottom width, side slope, DEM bed, and area-fit RMSE.

    The fit matches wetted area at several stages below the lower bank. Both
    parameters are positive, as required by the OpenHydroQual template.
    """
    samples = sorted(samples)
    if len(samples) < 5 or any(b[0] <= a[0] for a, b in zip(samples, samples[1:])):
        raise ValueError("A profile needs at least five unique, ordered offsets")
    if not left_bank_m < 0 < right_bank_m:
        raise ValueError("Reviewed bank offsets must straddle the centerline")
    interior = [(left_bank_m, _elevation(samples, left_bank_m))]
    interior += [(x, z) for x, z in samples if left_bank_m < x < right_bank_m]
    interior += [(right_bank_m, _elevation(samples, right_bank_m))]
    bed = min(z for _, z in interior)
    bank_depth = min(interior[0][1], interior[-1][1]) - bed
    if bank_depth < 0.25:
        raise ValueError("DEM bank-to-bed relief below 0.25 m; check hydroflattening or bank picks")
    depths = [bank_depth * fraction for fraction in (0.2, 0.4, 0.6, 0.8, 1.0)]
    areas = [_wetted_area(interior, bed + depth) for depth in depths]
    # A(h)/h = b + z*h: least-squares regression with area-weighted residuals.
    a11 = sum(h * h for h in depths)
    a12 = sum(h**3 for h in depths)
    a22 = sum(h**4 for h in depths)
    c1 = sum(h * area for h, area in zip(depths, areas))
    c2 = sum(h*h * area for h, area in zip(depths, areas))
    determinant = a11 * a22 - a12 * a12
    width = max(0.05, (c1 * a22 - c2 * a12) / determinant)
    slope = max(0.01, (c2 - width * a12) / a22)
    # Refit width after clamping to preserve area as closely as possible.
    width = max(0.05, (c1 - slope * a12) / a11)
    rmse = math.sqrt(sum((area - width*h - slope*h*h)**2 for area, h in zip(areas, depths)) / len(depths))
    if rmse > 0.2 * max(areas):
        raise ValueError("Trapezoid does not represent the DEM profile within 20% of bankfull area")
    if width + 2*slope*bank_depth > 1.5*(right_bank_m-left_bank_m):
        raise ValueError("Fitted channel top width greatly exceeds reviewed bank span")
    return width, slope, bed, rmse


def load_channel_segments(path: str | Path, reaches: dict[str, object]) -> dict[str, list[ChannelSegment]]:
    """Read profiles and split each matched reach at profile midpoints.

    All rows in one profile share reach_id, station_m and reviewed bank offsets.
    Reach names stay authoritative in the existing watershed topology.
    """
    by_id = {reach.id: (name, reach) for name, reach in reaches.items()}
    groups: dict[tuple[int, float], list[tuple[float, float]]] = defaultdict(list)
    banks: dict[tuple[int, float], tuple[float, float]] = {}
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"reach_id", "station_m", "offset_m", "elev_m", "bank_left_m", "bank_right_m"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Channel profiles require: " + ", ".join(sorted(required)))
        for row in reader:
            reach_id = int(_finite(row["reach_id"], "reach_id"))
            if reach_id not in by_id:
                raise ValueError(f"Unknown reach_id {reach_id} in channel profiles")
            station = _finite(row["station_m"], "station_m")
            key = (reach_id, station)
            current_banks = (_finite(row["bank_left_m"], "bank_left_m"),
                             _finite(row["bank_right_m"], "bank_right_m"))
            if key in banks and banks[key] != current_banks:
                raise ValueError(f"Inconsistent bank offsets for reach {reach_id}, station {station}")
            banks[key] = current_banks
            groups[key].append((_finite(row["offset_m"], "offset_m"),
                                _finite(row["elev_m"], "elev_m")))
    if not groups:
        raise ValueError("Channel profile CSV has no data")
    result: dict[str, list[ChannelSegment]] = {}
    for reach_id, (name, reach) in by_id.items():
        stations = sorted(s for rid, s in groups if rid == reach_id)
        if not stations:
            continue  # Other reaches retain their existing single segment.
        length = getattr(reach, "length_m", None)
        if length is None or not math.isfinite(length) or length <= 0:
            raise ValueError(f"Reach {name} needs a positive GIS length for segmentation")
        if stations[0] < 0 or stations[-1] > length:
            raise ValueError(f"Profile stations for {name} must lie within 0..{length} m")
        if any(b-a < 1.0 for a,b in zip(stations, stations[1:])):
            raise ValueError(f"Profile stations for {name} must be at least 1 m apart")
        z_up, z_dn = getattr(reach, "z_up_m", None), getattr(reach, "z_dn_m", None)
        if (z_up is None or z_dn is None or not math.isfinite(z_up) or
                not math.isfinite(z_dn) or abs(z_up-z_dn) < 1e-9):
            raise ValueError(f"Detailed channel {name} needs distinct, finite GIS endpoint elevations")
        edges = [0.0] + [(a+b)/2 for a,b in zip(stations, stations[1:])] + [length]
        segments = []
        for index, station in enumerate(stations):
            width, side, bed, rmse = fit_trapezoid(groups[(reach_id, station)], *banks[(reach_id, station)])
            segments.append(ChannelSegment(
                name=f"{name}__S{index+1:03d}", reach_id=reach_id,
                start_m=edges[index], end_m=edges[index+1],
                base_width_m=width, side_slope_z=side, bed_elevation_m=bed,
                fit_rmse_m2=rmse, profile_station_m=station,
            ))
        result[name] = segments
    return result


def station_on_reach(reach: object, x: float, y: float) -> float | None:
    """Project an outfall to its GIS centerline; return GIS station in metres."""
    points = getattr(reach, "centerline_xy", ())
    if len(points) < 2:
        start = (getattr(reach, "x_up_act", None), getattr(reach, "y_up_act", None))
        end = (getattr(reach, "x_dn_act", None), getattr(reach, "y_dn_act", None))
        if None in (*start, *end):
            return None
        points = (start, end)
    total = walked = 0.0
    closest_distance = float("inf")
    closest_station = 0.0
    lengths = [math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return None
    for a,b,span in zip(points, points[1:], lengths):
        if span <= 0:
            continue
        fraction = max(0.0, min(1.0, ((x-a[0])*(b[0]-a[0])+(y-a[1])*(b[1]-a[1]))/(span*span)))
        px, py = a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1])
        distance = math.hypot(px-x, py-y)
        if distance < closest_distance:
            closest_distance = distance
            closest_station = walked + fraction*span
        walked += span
    # Reject a location far from the mapped reach instead of guessing a segment.
    if closest_distance > max(30.0, 0.1 * total):
        return None
    return closest_station/total * reach.length_m


def position_on_reach(reach: object, fraction: float) -> tuple[float, float] | None:
    points = getattr(reach, "centerline_xy", ())
    if len(points) < 2:
        start = (getattr(reach, "x_up_act", None), getattr(reach, "y_up_act", None))
        end = (getattr(reach, "x_dn_act", None), getattr(reach, "y_dn_act", None))
        if None in (*start, *end):
            return None
        points = (start, end)
    lengths = [math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(points, points[1:])]
    station = max(0.0, min(1.0, fraction)) * sum(lengths)
    walked = 0.0
    for a,b,length in zip(points, points[1:], lengths):
        if walked + length >= station and length > 0:
            t = (station-walked)/length
            return (a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1]))
        walked += length
    return points[-1]


def segment_at_station(segments: list[ChannelSegment], station: float) -> str:
    edges = [segment.end_m for segment in segments]
    return segments[min(bisect_right(edges, station), len(segments)-1)].name


def write_fit_report(path: str | Path, segments: dict[str, list[ChannelSegment]],
                     reaches: dict[str, object]) -> None:
    """Write an auditable table of fitted geometry and assigned OHQ inverts."""
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("reach_name", "segment_name", "profile_station_m", "start_m", "end_m",
                         "base_width_m", "side_slope_z", "profile_dem_bed_m", "ohq_invert_m",
                         "area_fit_rmse_m2", "bathymetry_status"))
        for name, parts in segments.items():
            reach = reaches[name]
            for part in parts:
                z_up, z_dn = max(reach.z_up_m, reach.z_dn_m), min(reach.z_up_m, reach.z_dn_m)
                invert = z_up + (z_dn-z_up) * part.end_m/reach.length_m
                writer.writerow((name, part.name, part.profile_station_m, part.start_m, part.end_m,
                                 part.base_width_m, part.side_slope_z, part.bed_elevation_m, invert,
                                 part.fit_rmse_m2, "DEM_ONLY_UNVERIFIED"))

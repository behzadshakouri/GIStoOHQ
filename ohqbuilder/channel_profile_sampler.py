"""Sample candidate cross sections from a reach centerline and bare-earth DEM.

Bank fields are intentionally blank: the reviewer must identify the channel
limits before a terrain profile can be fitted as an OpenHydroQual trapezoid.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path


def sample_channel_profiles(reaches_path: str | Path, dem_path: str | Path,
                            output_path: str | Path, *, spacing_m: float = 50,
                            half_width_m: float = 30, sample_step_m: float = 1) -> int:
    if min(spacing_m, half_width_m, sample_step_m) <= 0:
        raise ValueError("Spacing, half width and sample step must be positive metres")
    import geopandas as gpd
    import rasterio
    from shapely.geometry import LineString

    reaches = gpd.read_file(reaches_path)
    if reaches.crs is None or not reaches.crs.is_projected:
        raise ValueError("Reach centerlines require a projected CRS in metres")
    unit = reaches.crs.axis_info[0].unit_name.lower()
    if unit not in {"metre", "meter", "metres", "meters"}:
        raise ValueError("Reach centerline coordinates must be in metres")
    rows = []
    with rasterio.open(dem_path) as dem:
        if dem.crs != rasterio.crs.CRS.from_wkt(reaches.crs.to_wkt()):
            raise ValueError("DEM and reach centerlines must use the same projected CRS")
        if max(abs(dem.res[0]), abs(dem.res[1])) > half_width_m:
            raise ValueError("DEM cells exceed the requested cross-section half width")
        for _, item in reaches.iterrows():
            rid = item.get("reach_id")
            if rid is None or not math.isfinite(float(rid)):
                raise ValueError("Every reach needs a numeric reach_id")
            geom = item.geometry
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "MultiLineString":
                geom = max(geom.geoms, key=lambda part: part.length)
            if geom.geom_type != "LineString" or geom.length < 2:
                continue
            z_up, z_dn = item.get("z_up_m"), item.get("z_dn_m")
            reverse = item.get("topo_orient") == "strong-elevation-reversal"
            if not reverse and z_up is not None and z_dn is not None:
                reverse = math.isfinite(float(z_up)) and math.isfinite(float(z_dn)) and float(z_dn)-float(z_up) >= 0.25 and (float(z_dn)-float(z_up))/geom.length >= 0.0001
            if reverse:
                geom = LineString(list(geom.coords)[::-1])
            reach_length = item.get("length_m")
            reach_length = float(reach_length) if reach_length is not None and math.isfinite(float(reach_length)) else geom.length
            if reach_length <= 0:
                raise ValueError(f"Reach {rid} has an invalid length")
            count = max(2, math.ceil(geom.length / spacing_m))
            for index in range(count):
                distance = geom.length * (index+0.5)/count
                point = geom.interpolate(distance)
                tangent_radius = min(spacing_m/4, geom.length/4, 10.0)
                before = geom.interpolate(max(0, distance-tangent_radius))
                after = geom.interpolate(min(geom.length, distance+tangent_radius))
                dx, dy = after.x-before.x, after.y-before.y
                magnitude = math.hypot(dx,dy)
                if magnitude == 0:
                    continue
                nx, ny = -dy/magnitude, dx/magnitude
                n_samples = math.ceil(2*half_width_m/sample_step_m)
                coords = []
                for i in range(n_samples+1):
                    offset = -half_width_m + 2*half_width_m*i/n_samples
                    coords.append((offset, point.x+nx*offset, point.y+ny*offset))
                elevations = list(dem.sample([(x,y) for _,x,y in coords], masked=True))
                for (offset,x,y), elevation in zip(coords, elevations):
                    z = elevation[0]
                    if (not bool(getattr(z, "mask", False)) and math.isfinite(float(z))
                            and (dem.nodata is None or float(z) != dem.nodata)):
                        rows.append((int(rid), round(distance/geom.length*reach_length, 6),
                                     round(offset, 6), float(z), "", "", x, y))
    if not rows:
        raise ValueError("No valid DEM elevations were sampled along the reaches")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("reach_id", "station_m", "offset_m", "elev_m", "bank_left_m",
                         "bank_right_m", "x", "y"))
        writer.writerows(rows)
    return len(rows)

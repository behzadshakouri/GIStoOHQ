#!/usr/bin/env python3
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point, LineString

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "examples" / "AZ12-100"
OUT = SITE / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

crs = "EPSG:32618"

pour = gpd.GeoDataFrame(
    {"id": [1], "name": ["P1"]},
    geometry=[Point(500500, 4300500)],
    crs=crs,
)
pour.to_file(OUT / "pour_points.shp")

reaches = gpd.GeoDataFrame(
    {
        "id": [1],
        "reach_id": [1],
        "ds_reach_id": [0],
        "name": ["Reach_1"],
        "from_node": ["J1"],
        "to_node": ["Outlet"],
        "length_m": [900.0],
    },
    geometry=[
        LineString([
            (500100, 4300900),
            (500300, 4300700),
            (500500, 4300500),
            (500700, 4300300),
        ])
    ],
    crs=crs,
)
reaches.to_file(OUT / "reaches.gpkg", layer="reaches", driver="GPKG")

junctions = gpd.GeoDataFrame(
    {
        "id": [1, 2],
        "name": ["J1", "Outlet"],
        "type": ["junction", "outlet"],
    },
    geometry=[
        Point(500100, 4300900),
        Point(500700, 4300300),
    ],
    crs=crs,
)
junctions.to_file(OUT / "junctions.gpkg", layer="junctions", driver="GPKG")

print("Created minimal phase2 inputs in:", OUT)

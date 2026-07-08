#!/usr/bin/env python3
from pathlib import Path
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]

outputs = ROOT / "outputs"
demlr = ROOT / "demlr"

outputs.mkdir(exist_ok=True)
demlr.mkdir(exist_ok=True)

crs = "EPSG:32618"  # UTM zone 18N; ok for testing near DC/MD/VA

# --------------------------------------------------
# 1. Create outlet.shp
# --------------------------------------------------
outlet = gpd.GeoDataFrame(
    {"id": [1], "name": ["Outlet"]},
    geometry=[Point(500500, 4300500)],
    crs=crs,
)

outlet_path = outputs / "outlet.shp"
outlet.to_file(outlet_path)

print(f"Created {outlet_path}")

# --------------------------------------------------
# 2. Create DEM raster: demlr/cliped_utm.tif
# --------------------------------------------------
width = 100
height = 100
cell_size = 10

x0 = 500000
y0 = 4301000

# Simple sloping DEM
dem = np.zeros((height, width), dtype=np.float32)
for row in range(height):
    for col in range(width):
        dem[row, col] = 100 + (height - row) * 0.5 + col * 0.05

transform = from_origin(x0, y0, cell_size, cell_size)

dem_path = demlr / "cliped_utm.tif"

with rasterio.open(
    dem_path,
    "w",
    driver="GTiff",
    height=height,
    width=width,
    count=1,
    dtype=dem.dtype,
    crs=crs,
    transform=transform,
    nodata=-9999,
) as dst:
    dst.write(dem, 1)

print(f"Created {dem_path}")

# --------------------------------------------------
# 3. Create clipped NHD-like flowline GeoPackage
# --------------------------------------------------
flowline = gpd.GeoDataFrame(
    {
        "id": [1],
        "name": ["Main_Channel"],
        "FCode": [46006],
        "length_m": [900.0],
    },
    geometry=[
        LineString(
            [
                (500100, 4300900),
                (500300, 4300700),
                (500500, 4300500),
                (500700, 4300300),
            ]
        )
    ],
    crs=crs,
)

flowline_path = outputs / "NHDFlowline_clip.gpkg"
flowline.to_file(flowline_path, layer="NHDFlowline_clip", driver="GPKG")

print(f"Created {flowline_path}")

print("\nMinimal required inputs created.")

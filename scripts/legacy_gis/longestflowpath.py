# =============================================================================
# longestflowpath.py
#
# Fast longest-flow-path calculation for GIStoOHQ / QGIS.
#
# Replaces the original cell-by-cell repeated downstream tracing algorithm.
# For each subwatershed this version:
#   1. rasterizes the polygon once to a local boolean mask;
#   2. starts at the subwatershed outlet;
#   3. walks upstream through cells that drain to the current cell;
#   4. visits each contributing cell only once;
#   5. reconstructs only the single longest path.
#
# Outputs:
#   outputs/longest_flow_paths.gpkg
#
# Updates in place:
#   outputs/subwatershed_params.gpkg
#
# Added/updated fields:
#   flow_len_ft
#   elev_max_ft
#   elev_min_ft
#   slope_lfp
#   slope_1085
#
# Expected GRASS r.watershed drainage coding:
#   1=NE, 2=N, 3=NW, 4=W, 5=SW, 6=S, 7=SE, 8=E
#
# Run through run_phase2.py or from the QGIS Python environment.
# =============================================================================

import json
import math
import os
from collections import deque

import numpy as np
from osgeo import gdal
from rasterio.features import rasterize
from rasterio.transform import Affine

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

gdal.UseExceptions()

# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

FLOWDIR_NAME = "flow_dir.tif"
ROUTING_DEM_NAME = "dem_carved.tif"

SUBWS_NAME = "subwatersheds.gpkg"
SUBWS_LAYER = "subwatersheds"

POUR_NAME = "pour_points_snapped.gpkg"

PARAMS_NAME = "subwatershed_params.gpkg"
PARAMS_LAYER = "subwatershed_params"

LFP_NAME = "longest_flow_paths.gpkg"
LFP_LAYER = "longest_flow_paths"

M_TO_FT = 3.280839895013123
ADD_TO_PROJECT = True

# Maximum number of cells used when snapping an outlet back into its polygon.
# This is only a safety guard. Normally the snapped point is already inside.
OUTLET_SEARCH_RADIUS_CELLS = 30

# -----------------------------------------------------------------------------
# GRASS flow-direction coding
# -----------------------------------------------------------------------------
# Row increases southward; column increases eastward.
GRASS_OFF = {
    1: (-1, 1),   # NE
    2: (-1, 0),   # N
    3: (-1, -1),  # NW
    4: (0, -1),   # W
    5: (1, -1),   # SW
    6: (1, 0),    # S
    7: (1, 1),    # SE
    8: (0, 1),    # E
}

# Reverse lookup used while walking upstream.
OFFSET_TO_CODE = {offset: code for code, offset in GRASS_OFF.items()}

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
site = os.path.join(ROOT, SITE_DIR)
out_dir = os.path.join(site, "outputs")

fd_path = os.path.join(out_dir, FLOWDIR_NAME)
routing_dem_path = os.path.join(out_dir, ROUTING_DEM_NAME)

# Prefer the unmodified DEM for elevations. It normally has the same grid as
# flow_dir.tif. Fall back to dem_carved.tif if it is unavailable or mismatched.
real_dem_path = os.path.join(site, "demlr", "cliped_utm.tif")

subws_path = os.path.join(out_dir, SUBWS_NAME)
pour_path = os.path.join(out_dir, POUR_NAME)
params_path = os.path.join(out_dir, PARAMS_NAME)
lfp_path = os.path.join(out_dir, LFP_NAME)

print("Site :", site)

for required_path in (fd_path, routing_dem_path, subws_path, pour_path, params_path):
    if not os.path.isfile(required_path):
        raise Exception("not found: " + required_path)

# -----------------------------------------------------------------------------
# Raster helpers
# -----------------------------------------------------------------------------
def open_single_band_array(path, dtype=None):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise Exception("could not open raster: " + path)

    arr = ds.GetRasterBand(1).ReadAsArray()
    if arr is None:
        raise Exception("could not read raster: " + path)

    if dtype is not None:
        arr = arr.astype(dtype, copy=False)

    return ds, arr


fd_ds, flow_dir = open_single_band_array(fd_path, np.int16)
gt = fd_ds.GetGeoTransform()
projection_wkt = fd_ds.GetProjection()
nx = fd_ds.RasterXSize
ny = fd_ds.RasterYSize

pixel_x = abs(float(gt[1]))
pixel_y = abs(float(gt[5]))
ortho_x = pixel_x
ortho_y = pixel_y
diag = math.hypot(pixel_x, pixel_y)

# Select elevation raster.
elev_path = real_dem_path if os.path.isfile(real_dem_path) else routing_dem_path
elev_ds, dem = open_single_band_array(elev_path, np.float32)

same_grid = (
    elev_ds.RasterXSize == nx
    and elev_ds.RasterYSize == ny
    and all(
        abs(float(a) - float(b)) <= 1.0e-7
        for a, b in zip(elev_ds.GetGeoTransform(), gt)
    )
)

if not same_grid:
    print(
        "WARNING: real DEM grid does not match flow_dir.tif; "
        "using dem_carved.tif for elevations."
    )
    elev_path = routing_dem_path
    elev_ds, dem = open_single_band_array(elev_path, np.float32)

print(
    "Grid : %d x %d  pixel %.3f x %.3f m"
    % (nx, ny, pixel_x, pixel_y)
)
print("Elevation raster:", elev_path)

# rasterio Affine corresponding to the GDAL geotransform.
full_transform = Affine.from_gdal(*gt)


def to_rc(x, y):
    """Map coordinates to zero-based raster row/column."""
    col = int(math.floor((x - gt[0]) / gt[1]))
    row = int(math.floor((y - gt[3]) / gt[5]))
    return row, col


def to_xy(row, col):
    """Return raster-cell center coordinates."""
    x = gt[0] + (col + 0.5) * gt[1] + (row + 0.5) * gt[2]
    y = gt[3] + (col + 0.5) * gt[4] + (row + 0.5) * gt[5]
    return float(x), float(y)


def clamp_window(r0, r1, c0, c1):
    r0 = max(0, min(ny - 1, int(r0)))
    r1 = max(0, min(ny - 1, int(r1)))
    c0 = max(0, min(nx - 1, int(c0)))
    c1 = max(0, min(nx - 1, int(c1)))

    if r0 > r1:
        r0, r1 = r1, r0
    if c0 > c1:
        c0, c1 = c1, c0

    return r0, r1, c0, c1


def geometry_window(geometry):
    """Return a raster window tightly covering a QGIS geometry."""
    bbox = geometry.boundingBox()

    rows_cols = [
        to_rc(bbox.xMinimum(), bbox.yMinimum()),
        to_rc(bbox.xMinimum(), bbox.yMaximum()),
        to_rc(bbox.xMaximum(), bbox.yMinimum()),
        to_rc(bbox.xMaximum(), bbox.yMaximum()),
    ]

    rows = [item[0] for item in rows_cols]
    cols = [item[1] for item in rows_cols]

    # One-cell padding prevents edge loss from floating-point rounding.
    return clamp_window(
        min(rows) - 1,
        max(rows) + 1,
        min(cols) - 1,
        max(cols) + 1,
    )


def window_transform(row0, col0):
    """Affine transform for a local raster window."""
    return full_transform * Affine.translation(col0, row0)


def rasterize_geometry(geometry, row0, row1, col0, col1):
    """Rasterize one subwatershed polygon into a local boolean mask."""
    height = row1 - row0 + 1
    width = col1 - col0 + 1

    geometry_json = json.loads(geometry.asJson())

    mask = rasterize(
        [(geometry_json, 1)],
        out_shape=(height, width),
        transform=window_transform(row0, col0),
        fill=0,
        all_touched=False,
        dtype="uint8",
    )

    return mask.astype(bool, copy=False)


def nearest_mask_cell(mask, row_local, col_local, max_radius):
    """
    Return the nearest True mask cell to the requested local row/column.

    First performs an expanding-window search. If that fails, it falls back to
    a vectorized nearest-cell search over the full local mask.
    """
    height, width = mask.shape

    if (
        0 <= row_local < height
        and 0 <= col_local < width
        and bool(mask[row_local, col_local])
    ):
        return row_local, col_local

    for radius in range(1, max_radius + 1):
        rr0 = max(0, row_local - radius)
        rr1 = min(height - 1, row_local + radius)
        cc0 = max(0, col_local - radius)
        cc1 = min(width - 1, col_local + radius)

        sub = mask[rr0 : rr1 + 1, cc0 : cc1 + 1]
        local_rows, local_cols = np.nonzero(sub)

        if local_rows.size:
            rows = local_rows + rr0
            cols = local_cols + cc0
            dist2 = (rows - row_local) ** 2 + (cols - col_local) ** 2
            index = int(np.argmin(dist2))
            return int(rows[index]), int(cols[index])

    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return None

    dist2 = (rows - row_local) ** 2 + (cols - col_local) ** 2
    index = int(np.argmin(dist2))
    return int(rows[index]), int(cols[index])


def step_length(drow, dcol):
    if drow != 0 and dcol != 0:
        return diag
    if drow != 0:
        return ortho_y
    return ortho_x


def trace_to_outlet(start_global, outlet_global, mask, row0, col0):
    """
    Reconstruct the downstream path from a start cell to the outlet.

    The traversal is constrained to the local subwatershed mask and includes
    loop detection plus a strict maximum-step guard.
    """
    start_row, start_col = start_global
    outlet_row, outlet_col = outlet_global

    path = [(start_row, start_col)]
    seen = {(start_row, start_col)}
    current_row = start_row
    current_col = start_col

    max_steps = int(mask.sum()) + 1

    for _ in range(max_steps):
        if (current_row, current_col) == (outlet_row, outlet_col):
            return path

        code = abs(int(flow_dir[current_row, current_col]))
        if code not in GRASS_OFF:
            return None

        drow, dcol = GRASS_OFF[code]
        next_row = current_row + drow
        next_col = current_col + dcol

        local_row = next_row - row0
        local_col = next_col - col0

        if not (
            0 <= local_row < mask.shape[0]
            and 0 <= local_col < mask.shape[1]
            and bool(mask[local_row, local_col])
        ):
            return None

        next_cell = (next_row, next_col)
        if next_cell in seen:
            return None

        path.append(next_cell)
        seen.add(next_cell)
        current_row, current_col = next_cell

    return None


def cumulative_distance(path):
    distance = np.zeros(len(path), dtype=np.float64)

    for index in range(1, len(path)):
        row0_, col0_ = path[index - 1]
        row1_, col1_ = path[index]
        distance[index] = (
            distance[index - 1]
            + step_length(row1_ - row0_, col1_ - col0_)
        )

    return distance


def interpolated_elevation(path, cumulative, fraction):
    """Linearly interpolate elevation at a fractional path distance."""
    total = float(cumulative[-1])
    if total <= 0.0:
        row, col = path[0]
        return float(dem[row, col])

    target = fraction * total
    index = int(np.searchsorted(cumulative, target, side="right") - 1)
    index = max(0, min(index, len(path) - 1))

    if index >= len(path) - 1:
        row, col = path[-1]
        return float(dem[row, col])

    d0 = float(cumulative[index])
    d1 = float(cumulative[index + 1])

    row_a, col_a = path[index]
    row_b, col_b = path[index + 1]

    elev_a = float(dem[row_a, col_a])
    elev_b = float(dem[row_b, col_b])

    if d1 <= d0:
        return elev_a

    weight = (target - d0) / (d1 - d0)
    return elev_a + weight * (elev_b - elev_a)


def slope_10_85(path, cumulative):
    total = float(cumulative[-1])
    if total <= 0.0:
        return 0.0

    elev_10 = interpolated_elevation(path, cumulative, 0.10)
    elev_85 = interpolated_elevation(path, cumulative, 0.85)

    return abs(elev_10 - elev_85) / (0.75 * total)


def find_longest_upstream_cell(mask, row0, col0, outlet_global):
    """
    Traverse upstream from the outlet.

    For every visited cell, inspect its eight neighbors. A neighboring cell is
    upstream of the current cell when that neighbor's flow-direction code points
    directly to the current cell.

    Returns:
        farthest_global_cell, farthest_distance_m, visited_count
    """
    height, width = mask.shape
    outlet_row, outlet_col = outlet_global
    outlet_local_row = outlet_row - row0
    outlet_local_col = outlet_col - col0

    distance = np.full((height, width), -1.0, dtype=np.float32)
    distance[outlet_local_row, outlet_local_col] = 0.0

    queue = deque()
    queue.append((outlet_local_row, outlet_local_col))

    farthest_local = (outlet_local_row, outlet_local_col)
    farthest_distance = 0.0
    visited_count = 0

    while queue:
        current_local_row, current_local_col = queue.popleft()
        current_global_row = current_local_row + row0
        current_global_col = current_local_col + col0
        current_distance = float(distance[current_local_row, current_local_col])
        visited_count += 1

        # Check all eight neighboring cells as possible upstream contributors.
        for drow in (-1, 0, 1):
            for dcol in (-1, 0, 1):
                if drow == 0 and dcol == 0:
                    continue

                neighbor_local_row = current_local_row + drow
                neighbor_local_col = current_local_col + dcol

                if not (
                    0 <= neighbor_local_row < height
                    and 0 <= neighbor_local_col < width
                ):
                    continue

                if not bool(mask[neighbor_local_row, neighbor_local_col]):
                    continue

                if distance[neighbor_local_row, neighbor_local_col] >= 0.0:
                    continue

                neighbor_global_row = neighbor_local_row + row0
                neighbor_global_col = neighbor_local_col + col0

                code = abs(
                    int(flow_dir[neighbor_global_row, neighbor_global_col])
                )
                if code not in GRASS_OFF:
                    continue

                flow_drow, flow_dcol = GRASS_OFF[code]

                # The neighbor is upstream only when it drains to current.
                if (
                    neighbor_global_row + flow_drow != current_global_row
                    or neighbor_global_col + flow_dcol != current_global_col
                ):
                    continue

                new_distance = (
                    current_distance + step_length(flow_drow, flow_dcol)
                )

                distance[
                    neighbor_local_row, neighbor_local_col
                ] = new_distance

                queue.append((neighbor_local_row, neighbor_local_col))

                if new_distance > farthest_distance:
                    farthest_distance = new_distance
                    farthest_local = (
                        neighbor_local_row,
                        neighbor_local_col,
                    )

    farthest_global = (
        farthest_local[0] + row0,
        farthest_local[1] + col0,
    )

    return farthest_global, farthest_distance, visited_count


# -----------------------------------------------------------------------------
# Load subwatersheds and pour points
# -----------------------------------------------------------------------------
subwatersheds = QgsVectorLayer(
    subws_path + "|layername=" + SUBWS_LAYER,
    "subwatersheds",
    "ogr",
)
if not subwatersheds.isValid():
    subwatersheds = QgsVectorLayer(subws_path, "subwatersheds", "ogr")

pour_points = QgsVectorLayer(pour_path, "pour_points_snapped", "ogr")

if not subwatersheds.isValid():
    raise Exception("could not open subwatersheds: " + subws_path)
if not pour_points.isValid():
    raise Exception("could not open pour points: " + pour_path)

pour_features = [
    feature
    for feature in pour_points.getFeatures()
    if not feature.geometry().isEmpty()
]

if not pour_features:
    raise Exception("no valid pour points found: " + pour_path)


def choose_outlet_point(subwatershed_geometry, subwatershed_id):
    """
    Prefer a pour point whose id matches the subwatershed id.

    Then try polygon containment. Finally use the nearest pour point.
    """
    # Exact id match is the safest choice when ids are available.
    for feature in pour_features:
        if "id" in feature.fields().names():
            value = feature["id"]
            if value is not None and str(value) == str(subwatershed_id):
                return feature.geometry().asPoint()

    for feature in pour_features:
        point_geometry = feature.geometry()
        if subwatershed_geometry.intersects(point_geometry):
            return point_geometry.asPoint()

    nearest_feature = min(
        pour_features,
        key=lambda feature: subwatershed_geometry.distance(feature.geometry()),
    )
    return nearest_feature.geometry().asPoint()


# -----------------------------------------------------------------------------
# Per-subwatershed processing
# -----------------------------------------------------------------------------
results = {}
line_points = {}

feature_count = subwatersheds.featureCount()
print("Subwatersheds:", feature_count)

for index, subwatershed in enumerate(subwatersheds.getFeatures(), start=1):
    sid = int(subwatershed["id"])
    geometry = subwatershed.geometry()

    if geometry.isEmpty():
        print("  [%d/%d] id %s: EMPTY geometry" % (index, feature_count, sid))
        results[sid] = {
            "flow_len_ft": None,
            "elev_max_ft": None,
            "elev_min_ft": None,
            "slope_lfp": None,
            "slope_1085": None,
        }
        continue

    if not geometry.isGeosValid():
        geometry = geometry.makeValid()

    row0, row1, col0, col1 = geometry_window(geometry)
    mask = rasterize_geometry(geometry, row0, row1, col0, col1)

    mask_cell_count = int(mask.sum())
    if mask_cell_count == 0:
        print("  [%d/%d] id %s: polygon rasterized to zero cells" %
              (index, feature_count, sid))
        results[sid] = {
            "flow_len_ft": None,
            "elev_max_ft": None,
            "elev_min_ft": None,
            "slope_lfp": None,
            "slope_1085": None,
        }
        continue

    outlet_point = choose_outlet_point(geometry, sid)
    outlet_row, outlet_col = to_rc(outlet_point.x(), outlet_point.y())

    outlet_local_row = outlet_row - row0
    outlet_local_col = outlet_col - col0

    snapped_local = nearest_mask_cell(
        mask,
        outlet_local_row,
        outlet_local_col,
        OUTLET_SEARCH_RADIUS_CELLS,
    )

    if snapped_local is None:
        print("  [%d/%d] id %s: no valid outlet cell" %
              (index, feature_count, sid))
        results[sid] = {
            "flow_len_ft": None,
            "elev_max_ft": None,
            "elev_min_ft": None,
            "slope_lfp": None,
            "slope_1085": None,
        }
        continue

    snapped_local_row, snapped_local_col = snapped_local
    outlet_global = (
        snapped_local_row + row0,
        snapped_local_col + col0,
    )

    outlet_shift_m = math.hypot(
        (snapped_local_col - outlet_local_col) * pixel_x,
        (snapped_local_row - outlet_local_row) * pixel_y,
    )

    farthest_global, longest_distance_m, visited_count = (
        find_longest_upstream_cell(
            mask,
            row0,
            col0,
            outlet_global,
        )
    )

    path = trace_to_outlet(
        farthest_global,
        outlet_global,
        mask,
        row0,
        col0,
    )

    if not path or len(path) < 2 or longest_distance_m <= 0.0:
        print(
            "  [%d/%d] id %s: no upstream path reached outlet "
            "(mask=%d, reached=%d)"
            % (
                index,
                feature_count,
                sid,
                mask_cell_count,
                visited_count,
            )
        )
        results[sid] = {
            "flow_len_ft": None,
            "elev_max_ft": None,
            "elev_min_ft": None,
            "slope_lfp": None,
            "slope_1085": None,
        }
        continue

    cumulative = cumulative_distance(path)
    measured_length_m = float(cumulative[-1])

    # The reverse traversal and reconstructed path should agree.
    if abs(measured_length_m - longest_distance_m) > max(pixel_x, pixel_y) * 1.5:
        print(
            "    WARNING id %s: reverse distance %.2f m differs from "
            "reconstructed distance %.2f m"
            % (sid, longest_distance_m, measured_length_m)
        )

    start_row, start_col = path[0]
    end_row, end_col = path[-1]

    upstream_elevation_m = float(dem[start_row, start_col])
    outlet_elevation_m = float(dem[end_row, end_col])

    if not np.isfinite(upstream_elevation_m):
        upstream_elevation_m = float("nan")
    if not np.isfinite(outlet_elevation_m):
        outlet_elevation_m = float("nan")

    slope_lfp = (
        abs(upstream_elevation_m - outlet_elevation_m) / measured_length_m
        if (
            measured_length_m > 0.0
            and np.isfinite(upstream_elevation_m)
            and np.isfinite(outlet_elevation_m)
        )
        else None
    )

    slope_1085_value = (
        slope_10_85(path, cumulative)
        if (
            np.isfinite(upstream_elevation_m)
            and np.isfinite(outlet_elevation_m)
        )
        else None
    )

    flow_length_ft = measured_length_m * M_TO_FT
    elev_max_ft = (
        upstream_elevation_m * M_TO_FT
        if np.isfinite(upstream_elevation_m)
        else None
    )
    elev_min_ft = (
        outlet_elevation_m * M_TO_FT
        if np.isfinite(outlet_elevation_m)
        else None
    )

    results[sid] = {
        "flow_len_ft": round(flow_length_ft, 1),
        "elev_max_ft": round(elev_max_ft, 1)
        if elev_max_ft is not None
        else None,
        "elev_min_ft": round(elev_min_ft, 1)
        if elev_min_ft is not None
        else None,
        "slope_lfp": round(slope_lfp, 6)
        if slope_lfp is not None
        else None,
        "slope_1085": round(slope_1085_value, 6)
        if slope_1085_value is not None
        else None,
    }

    line_points[sid] = [to_xy(row, col) for row, col in path]

    slope_lfp_pct = (
        100.0 * slope_lfp if slope_lfp is not None else float("nan")
    )
    slope_1085_pct = (
        100.0 * slope_1085_value
        if slope_1085_value is not None
        else float("nan")
    )

    print(
        "  [%d/%d] id %s: mask=%d reached=%d outlet_shift=%.1f m "
        "L=%.0f ft slope_lfp=%.3f%% s1085=%.3f%%"
        % (
            index,
            feature_count,
            sid,
            mask_cell_count,
            visited_count,
            outlet_shift_m,
            flow_length_ft,
            slope_lfp_pct,
            slope_1085_pct,
        )
    )

# -----------------------------------------------------------------------------
# Write longest-flow-path lines
# -----------------------------------------------------------------------------
fields = QgsFields()
fields.append(QgsField("id", QVariant.Int))
fields.append(QgsField("flow_len_ft", QVariant.Double))
fields.append(QgsField("slope_lfp", QVariant.Double))
fields.append(QgsField("slope_1085", QVariant.Double))

if os.path.exists(lfp_path):
    try:
        os.remove(lfp_path)
    except OSError:
        pass

options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = LFP_LAYER

writer = QgsVectorFileWriter.create(
    lfp_path,
    fields,
    QgsWkbTypes.LineString,
    subwatersheds.crs(),
    QgsCoordinateTransformContext(),
    options,
)

if writer.hasError() != QgsVectorFileWriter.NoError:
    raise Exception(
        "could not create %s: %s"
        % (lfp_path, writer.errorMessage())
    )

for sid, points in line_points.items():
    feature = QgsFeature(fields)
    feature.setGeometry(
        QgsGeometry.fromPolylineXY(
            [QgsPointXY(x, y) for x, y in points]
        )
    )
    feature["id"] = int(sid)
    feature["flow_len_ft"] = results[sid]["flow_len_ft"]
    feature["slope_lfp"] = results[sid]["slope_lfp"]
    feature["slope_1085"] = results[sid]["slope_1085"]

    if not writer.addFeature(feature):
        raise Exception("failed writing longest flow path for id %s" % sid)

del writer

# -----------------------------------------------------------------------------
# Update subwatershed_params.gpkg in place
# -----------------------------------------------------------------------------
params_layer = QgsVectorLayer(
    params_path + "|layername=" + PARAMS_LAYER,
    PARAMS_LAYER,
    "ogr",
)

if not params_layer.isValid():
    raise Exception("could not open parameter layer: " + params_path)

new_columns = [
    "flow_len_ft",
    "elev_max_ft",
    "elev_min_ft",
    "slope_lfp",
    "slope_1085",
]

existing_names = params_layer.fields().names()
missing_fields = [
    QgsField(column, QVariant.Double)
    for column in new_columns
    if column not in existing_names
]

if missing_fields:
    if not params_layer.dataProvider().addAttributes(missing_fields):
        raise Exception("could not add longest-flow-path fields")
    params_layer.updateFields()

field_indexes = {
    column: params_layer.fields().indexFromName(column)
    for column in new_columns
}

if not params_layer.startEditing():
    raise Exception("could not start editing: " + params_path)

for feature in params_layer.getFeatures():
    value = feature["id"]
    sid = int(value) if value is not None else None
    result = results.get(sid)

    if result is None:
        continue

    for column in new_columns:
        params_layer.changeAttributeValue(
            feature.id(),
            field_indexes[column],
            result[column],
        )

if not params_layer.commitChanges():
    errors = params_layer.commitErrors()
    params_layer.rollBack()
    raise Exception(
        "failed updating %s: %s"
        % (params_path, "; ".join(errors))
    )

print("")
print("Wrote :", lfp_path)
print("Updated:", params_path)

if ADD_TO_PROJECT:
    for layer_path, layer_name, display_name in (
        (lfp_path, LFP_LAYER, LFP_LAYER),
        (params_path, PARAMS_LAYER, PARAMS_LAYER),
    ):
        layer = QgsVectorLayer(
            layer_path + "|layername=" + layer_name,
            display_name,
            "ogr",
        )
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)

print("")
print("Done.")

# =============================================================================
# delineate_whole_watershed.py   (QGIS Python Console)
#
# PHASE 1, step 2: delineate the ENTIRE watershed from a SINGLE outlet point and
# write it directly as watershed_boundary.gpkg (the outer-boundary polygon that
# extract_reaches.py clips the stream network to). No subwatersheds here -- this
# is the whole-site catchment used to find reaches + junctions BEFORE the
# operator places interior pour points by hand.
#
# Differs from delineatewatershed.py (which makes one polygon per pour point and
# is used in phase 2): this reads exactly one point from outlet.shp, snaps it to
# the channel, delineates once, and writes watershed_boundary.gpkg with the CRS
# stamped from the flow-direction grid.
#
# INPUT
#   OUTLET_PATH, defaulting to <OUT_DIR>/outlet.shp
#   FLOWDIR_PATH, defaulting to <OUT_DIR>/flow_dir.tif
#   FLOWACC_PATH, defaulting to <OUT_DIR>/flow_acc.tif
#
# OUTPUT
#   BOUNDARY_PATH, defaulting to <OUT_DIR>/watershed_boundary.gpkg
#   SNAPPED_OUTLET_PATH, defaulting to <OUT_DIR>/outlet_snapped.gpkg
#   scratch rasters/gpkgs in <OUT_DIR>/temp/
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import importlib
import importlib.util
import os
import sys
import time

import numpy as np
import processing
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant


# =============================================================================
# RUNNER-OVERRIDABLE SETTINGS
# =============================================================================

ROOT = globals().get(
    "ROOT",
    "/mnt/3rd900/Projects/SligoCreek_QGIS",
)
SITE_DIR = globals().get("SITE_DIR", "")
SCRIPT_DIR = globals().get("SCRIPT_DIR", None)

OUTLET_REL = globals().get("OUTLET_REL", "outlet.shp")
FLOWDIR_REL = globals().get("FLOWDIR_REL", "flow_dir.tif")
FLOWACC_REL = globals().get("FLOWACC_REL", "flow_acc.tif")

SNAP = bool(globals().get("SNAP", True))
SNAP_RADIUS_M = float(globals().get("SNAP_RADIUS_M", 150.0))
SNAP_DISTANCE_WEIGHT = float(globals().get("SNAP_DISTANCE_WEIGHT", 0.0))
MIN_SNAP_ACC_CELLS = float(globals().get("MIN_SNAP_ACC_CELLS", 50.0))
SNAP_EDGE_FRACTION = float(globals().get("SNAP_EDGE_FRACTION", 0.90))
MIN_WATERSHED_AREA_KM2 = float(
    globals().get("MIN_WATERSHED_AREA_KM2", 0.01)
)
FALLBACK_EPSG = int(globals().get("FALLBACK_EPSG", 26912))
ADD_TO_PROJECT = bool(globals().get("ADD_TO_PROJECT", False))


# =============================================================================
# PATH RESOLUTION
# =============================================================================

ROOT = os.path.abspath(os.path.expanduser(ROOT))

if SCRIPT_DIR is None:
    if "__file__" in globals():
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    else:
        SCRIPT_DIR = os.getcwd()
SCRIPT_DIR = os.path.abspath(os.path.expanduser(SCRIPT_DIR))

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from ws3io import release_and_delete  # noqa: E402

if os.path.isabs(SITE_DIR):
    site_path = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    site_path = os.path.abspath(os.path.join(ROOT, SITE_DIR))

OUT_DIR = globals().get("OUT_DIR", os.path.join(site_path, "outputs"))
OUT_DIR = os.path.abspath(os.path.expanduser(OUT_DIR))
TEMP_DIR = os.path.join(OUT_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

OUTLET_PATH = globals().get("OUTLET_PATH", os.path.join(OUT_DIR, OUTLET_REL))
OUTLET_PATH = os.path.abspath(os.path.expanduser(OUTLET_PATH))
FLOWDIR_PATH = globals().get("FLOWDIR_PATH", os.path.join(OUT_DIR, FLOWDIR_REL))
FLOWDIR_PATH = os.path.abspath(os.path.expanduser(FLOWDIR_PATH))
FLOWACC_PATH = globals().get("FLOWACC_PATH", os.path.join(OUT_DIR, FLOWACC_REL))
FLOWACC_PATH = os.path.abspath(os.path.expanduser(FLOWACC_PATH))
BOUNDARY_OUT = globals().get(
    "BOUNDARY_PATH",
    os.path.join(OUT_DIR, "watershed_boundary.gpkg"),
)
BOUNDARY_OUT = os.path.abspath(os.path.expanduser(BOUNDARY_OUT))
SNAPPED_OUT = globals().get(
    "SNAPPED_OUTLET_PATH",
    os.path.join(OUT_DIR, "outlet_snapped.gpkg"),
)
SNAPPED_OUT = os.path.abspath(os.path.expanduser(SNAPPED_OUT))


# =============================================================================
# HELPERS
# =============================================================================

def _module_spec_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _register_grass_provider():
    registry = QgsApplication.processingRegistry()
    if registry.algorithmById("grass:r.watershed") or registry.algorithmById("grass7:r.watershed"):
        return
    for plugin_path in (
        "/usr/share/qgis/python/plugins",
        os.path.join(sys.prefix, "share", "qgis", "python", "plugins"),
    ):
        if os.path.isdir(plugin_path) and plugin_path not in sys.path:
            sys.path.insert(0, plugin_path)
    provider_specs = (
        ("grassprovider.Grass7AlgorithmProvider", "Grass7AlgorithmProvider"),
        ("grassprovider.GrassProvider", "GrassProvider"),
        ("processing.algs.grass7.Grass7AlgorithmProvider", "Grass7AlgorithmProvider"),
        ("processing.algs.grass.GrassAlgorithmProvider", "GrassAlgorithmProvider"),
    )
    for module_name, class_name in provider_specs:
        if not _module_spec_available(module_name):
            continue
        module = importlib.import_module(module_name)
        provider_class = getattr(module, class_name)
        provider = provider_class()
        load = getattr(provider, "load", None)
        if load is not None:
            load()
        registry.addProvider(provider)
        if registry.algorithmById("grass:r.watershed") or registry.algorithmById("grass7:r.watershed"):
            return


def grass_id(name):
    _register_grass_provider()
    from qgis.core import QgsApplication

    registry = QgsApplication.processingRegistry()
    for prefix in ("grass7:", "grass:"):
        algorithm_id = prefix + name
        if registry.algorithmById(algorithm_id):
            return algorithm_id
    return "grass7:" + name


def wipe_temp_dir(temp_dir):
    """Clear scratch files from temp/ after releasing loaded QGIS layers."""
    project = QgsProject.instance()
    normalized_temp = os.path.normcase(os.path.abspath(temp_dir))

    for layer in list(project.mapLayers().values()):
        try:
            source = os.path.normcase(
                os.path.abspath(layer.source().split("|", 1)[0])
            )
            if source.startswith(normalized_temp):
                project.removeMapLayer(layer.id())
        except Exception:
            pass

    if not os.path.isdir(temp_dir):
        return

    for _attempt in range(8):
        leftovers = [
            os.path.join(temp_dir, filename)
            for filename in os.listdir(temp_dir)
        ]
        if not leftovers:
            return

        for path in leftovers:
            try:
                os.remove(path)
            except OSError:
                pass

        if not os.listdir(temp_dir):
            return

        time.sleep(1.0)

    remaining = os.listdir(temp_dir)
    if remaining:
        raise Exception(
            "temp/ still holds locked file(s): %s\nRestart QGIS to release "
            "handles from a crashed run, then re-run."
            % ", ".join(remaining[:5])
        )


def snap_to_flow_accumulation(x0, y0, raster_path, radius_m):
    """Snap to the greatest absolute accumulation within a circular radius."""
    dataset = gdal.Open(raster_path)
    if dataset is None:
        raise Exception(
            "Could not open flow accumulation raster: " + raster_path
        )

    band = dataset.GetRasterBand(1)
    geotransform = dataset.GetGeoTransform()
    nx = dataset.RasterXSize
    ny = dataset.RasterYSize
    nodata = band.GetNoDataValue()

    origin_x, pixel_w, rotation_x, origin_y, rotation_y, pixel_h = geotransform
    if rotation_x != 0 or rotation_y != 0:
        dataset = None
        raise Exception("Rotated flow-accumulation rasters are not supported.")
    if pixel_w == 0 or pixel_h == 0:
        dataset = None
        raise Exception("Invalid flow-accumulation geotransform.")

    col0 = int(np.floor((x0 - origin_x) / pixel_w))
    row0 = int(np.floor((y0 - origin_y) / pixel_h))
    if not (0 <= col0 < nx and 0 <= row0 < ny):
        dataset = None
        raise Exception(
            "Outlet falls outside flow_acc.tif after CRS transformation: "
            "%.2f, %.2f" % (x0, y0)
        )

    radius_cols = max(1, int(np.ceil(radius_m / abs(pixel_w))))
    radius_rows = max(1, int(np.ceil(radius_m / abs(pixel_h))))
    c0 = max(0, col0 - radius_cols)
    c1 = min(nx, col0 + radius_cols + 1)
    r0 = max(0, row0 - radius_rows)
    r1 = min(ny, row0 + radius_rows + 1)

    subset = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype("float64")
    dataset = None

    valid = np.isfinite(subset)
    if nodata is not None:
        valid &= subset != nodata

    row_indices, col_indices = np.indices(subset.shape)
    cell_x = origin_x + (c0 + col_indices + 0.5) * pixel_w
    cell_y = origin_y + (r0 + row_indices + 0.5) * pixel_h
    distance = np.hypot(cell_x - x0, cell_y - y0)
    valid &= distance <= radius_m

    if not np.any(valid):
        raise Exception(
            "No valid flow-accumulation cells found within %.1f m of outlet."
            % radius_m
        )

    magnitude = np.abs(subset)
    score = magnitude.copy()
    if SNAP_DISTANCE_WEIGHT > 0:
        score = score / (1.0 + SNAP_DISTANCE_WEIGHT * distance)
    score[~valid] = -np.inf

    flat_index = int(np.argmax(score))
    local_row, local_col = np.unravel_index(flat_index, score.shape)
    snap_col = c0 + local_col
    snap_row = r0 + local_row
    snap_x = origin_x + (snap_col + 0.5) * pixel_w
    snap_y = origin_y + (snap_row + 0.5) * pixel_h
    raw_accumulation = float(subset[local_row, local_col])
    moved = float(np.hypot(snap_x - x0, snap_y - y0))

    return snap_x, snap_y, raw_accumulation, moved, snap_col, snap_row


def print_alignment_guidance(snap_acc=None, moved=None):
    """Print field checks for outlet/routing-grid alignment failures."""
    print("\nOUTLET / ROUTING-GRID ALIGNMENT CHECK")
    print("  The runner and preflight passed; this is a routing alignment issue.")
    if moved is not None:
        print("  snap movement      : %.2f m of %.2f m" % (moved, SNAP_RADIUS_M))
    if snap_acc is not None:
        print("  snapped |flow_acc| : %.3f cells" % abs(snap_acc))
    print("\n  Check these layers together in QGIS:")
    print("   ", OUTLET_PATH)
    print("   ", FLOWACC_PATH)
    print("   ", FLOWDIR_PATH)
    print("   ", os.path.join(site_path, "clipped_dem_utm.tif"))
    print("\n  Use Identify Features on flow_acc.tif along the intended channel near")
    print("  the outlet. A real downstream channel cell should have accumulation")
    print("  far greater than the small snapped value shown above.")
    print("\n  Safest correction:")
    print("   1. Display flow_acc.tif with a stretched/log renderer.")
    print("   2. Move outlet.shp directly onto the highest-accumulation raster cell")
    print("      that matches the intended NHD/channel centerline.")
    print("   3. Save outlet.shp in the routing-grid CRS (%s)." % crs_authid)
    print("   4. Rerun the same Phase 1 runner.")
    print("\n  Do not lower MIN_WATERSHED_AREA_KM2 just to continue; it is preventing")
    print("  a meaningless tiny watershed from propagating through later steps.")


# =============================================================================
# PREFLIGHT / INPUTS
# =============================================================================

wipe_temp_dir(TEMP_DIR)

print("Site       :", site_path)
print("Outlet     :", OUTLET_PATH)
print("Flow dir   :", FLOWDIR_PATH)
print("Flow acc   :", FLOWACC_PATH)
print("Boundary   :", BOUNDARY_OUT)
print("Snap       :", SNAP, "| radius:", SNAP_RADIUS_M, "m")

for path in (OUTLET_PATH, FLOWDIR_PATH, FLOWACC_PATH):
    if not os.path.isfile(path):
        raise Exception("not found: " + path)

fdir = QgsRasterLayer(FLOWDIR_PATH, "flow_dir")
if not fdir.isValid():
    raise Exception("Flow direction raster invalid: " + FLOWDIR_PATH)

grid_crs = fdir.crs()
if not grid_crs.isValid():
    grid_crs = QgsCoordinateReferenceSystem("EPSG:%d" % FALLBACK_EPSG)
    print("  flow_dir lacked CRS -> using EPSG:%d" % FALLBACK_EPSG)
crs_authid = grid_crs.authid() or ("EPSG:%d" % FALLBACK_EPSG)

points = QgsVectorLayer(OUTLET_PATH, "outlet", "ogr")
if not points.isValid():
    raise Exception("Outlet layer invalid: " + OUTLET_PATH)

features = list(points.getFeatures())
if len(features) == 0:
    raise Exception("outlet.shp has no features.")
if len(features) > 1:
    print(
        "  NOTE: outlet.shp has %d features; using the FIRST one only."
        % len(features)
    )

feature = features[0]
geometry = feature.geometry()
if geometry.isEmpty():
    raise Exception("outlet feature has empty geometry.")

if not points.crs().isValid():
    raise Exception("Outlet layer has no valid CRS: " + OUTLET_PATH)

point = geometry.asPoint()
source_point = QgsPointXY(point.x(), point.y())
if points.crs() != grid_crs:
    transform = QgsCoordinateTransform(
        points.crs(),
        grid_crs,
        QgsProject.instance(),
    )
    grid_point = transform.transform(source_point)
    print(
        "\nOutlet CRS transformed: %s -> %s"
        % (points.crs().authid(), crs_authid)
    )
else:
    grid_point = source_point

x = grid_point.x()
y = grid_point.y()
print(
    "\nOriginal outlet on routing grid: %.2f, %.2f (%s)"
    % (x, y, crs_authid)
)

snap_acc = None
moved = None

if SNAP:
    x, y, snap_acc, moved, snap_col, snap_row = snap_to_flow_accumulation(
        x,
        y,
        FLOWACC_PATH,
        SNAP_RADIUS_M,
    )
    print("Automatic outlet snap:")
    print("  search radius       : %.1f m" % SNAP_RADIUS_M)
    print("  snapped coordinate  : %.2f, %.2f" % (x, y))
    print("  raster cell          : col %d, row %d" % (snap_col, snap_row))
    print("  raw flow accumulation: %.3f" % snap_acc)
    print("  |flow accumulation|  : %.3f cells" % abs(snap_acc))
    print("  movement             : %.2f m" % moved)
    if moved >= SNAP_EDGE_FRACTION * SNAP_RADIUS_M:
        print(
            "  WARNING: snap used %.0f%% or more of the search radius; "
            "the intended routed channel may not be near the outlet."
            % (SNAP_EDGE_FRACTION * 100.0)
        )
    if abs(snap_acc) < MIN_SNAP_ACC_CELLS:
        print(
            "  WARNING: snapped accumulation is below %.0f cells; "
            "the operator point may not be near the intended channel."
            % MIN_SNAP_ACC_CELLS
        )
    if (
        moved >= SNAP_EDGE_FRACTION * SNAP_RADIUS_M
        and abs(snap_acc) < MIN_SNAP_ACC_CELLS
    ):
        print_alignment_guidance(snap_acc=snap_acc, moved=moved)
else:
    print("Automatic outlet snapping is disabled; using operator point as-is.")


# =============================================================================
# DELINEATE: r.water.outlet -> polygonize -> select DN=1 -> dissolve
# =============================================================================

wat_ras = os.path.join(TEMP_DIR, "whole_wshed.tif")
wat_vec = os.path.join(TEMP_DIR, "whole_wshed.gpkg")

print("\nDelineating whole watershed at %.2f, %.2f ..." % (x, y))
processing.run(
    grass_id("r.water.outlet"),
    {
        "input": FLOWDIR_PATH,
        "coordinates": "%f,%f" % (x, y),
        "output": wat_ras,
        "GRASS_REGION_PARAMETER": None,
        "GRASS_REGION_CELLSIZE_PARAMETER": 0,
        "GRASS_RASTER_FORMAT_OPT": "",
        "GRASS_RASTER_FORMAT_META": "",
    },
)

# The gdal:polygonize processing wrapper can silently fail on some installs, so
# call gdal.Polygonize() directly and write the GPKG via OGR.
release_and_delete(wat_vec)
source = gdal.Open(wat_ras)
band = source.GetRasterBand(1)
spatial_reference = osr.SpatialReference()
spatial_reference.ImportFromWkt(source.GetProjection())
dataset = ogr.GetDriverByName("GPKG").CreateDataSource(wat_vec)
layer = dataset.CreateLayer(
    "whole_wshed",
    srs=spatial_reference,
    geom_type=ogr.wkbPolygon,
)
layer.CreateField(ogr.FieldDefn("DN", ogr.OFTInteger))
gdal.Polygonize(band, band.GetMaskBand(), layer, 0, [], callback=None)
dataset = None
source = None

poly_layer = QgsVectorLayer(
    wat_vec + "|layername=whole_wshed",
    "whole_wshed_poly",
    "ogr",
)
if not poly_layer.isValid() or poly_layer.featureCount() == 0:
    raise Exception("gdal.Polygonize produced no polygons: " + wat_vec)

selected = processing.run(
    "native:extractbyexpression",
    {
        "INPUT": poly_layer,
        "EXPRESSION": '"DN" = 1',
        "OUTPUT": "TEMPORARY_OUTPUT",
    },
)
dissolved = processing.run(
    "native:dissolve",
    {
        "INPUT": selected["OUTPUT"],
        "FIELD": [],
        "OUTPUT": "TEMPORARY_OUTPUT",
    },
)["OUTPUT"]

# Collect dissolved geometry and write watershed_boundary.gpkg WITH CRS.
dissolved_layer = (
    QgsVectorLayer(dissolved, "diss", "ogr")
    if isinstance(dissolved, str)
    else dissolved
)
geometries = [
    item.geometry()
    for item in dissolved_layer.getFeatures()
    if not item.geometry().isEmpty()
]
if not geometries:
    raise Exception("delineation produced no polygon (outlet may be off-channel).")

boundary = QgsGeometry.unaryUnion(geometries).makeValid()
area_km2 = boundary.area() / 1e6
print("Whole-watershed area: %.4f km2" % area_km2)
if area_km2 < MIN_WATERSHED_AREA_KM2:
    print_alignment_guidance(snap_acc=snap_acc, moved=moved)
    raise Exception(
        "Delineated watershed area %.4f km2 is below the sanity threshold "
        "%.4f km2. The outlet/routing grid alignment is not producing a "
        "meaningful drainage path. Move outlet.shp onto a high-accumulation "
        "cell on the intended channel and rerun Phase 1; do not lower the "
        "sanity threshold to continue."
        % (area_km2, MIN_WATERSHED_AREA_KM2)
    )

project = QgsProject.instance()
release_and_delete(BOUNDARY_OUT)

fields = QgsFields()
fields.append(QgsField("id", QVariant.Int))
fields.append(QgsField("area_km2", QVariant.Double))
options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = "watershed_boundary"
boundary_geometry = boundary
if boundary_geometry.wkbType() not in (
    QgsWkbTypes.MultiPolygon,
    QgsWkbTypes.Polygon,
):
    coerced = boundary_geometry.coerceToType(QgsWkbTypes.MultiPolygon)
    if coerced:
        boundary_geometry = coerced[0]

writer = QgsVectorFileWriter.create(
    BOUNDARY_OUT,
    fields,
    QgsWkbTypes.MultiPolygon,
    grid_crs,
    QgsCoordinateTransformContext(),
    options,
)
feature_out = QgsFeature(fields)
feature_out.setGeometry(boundary_geometry)
feature_out["id"] = 1
feature_out["area_km2"] = round(area_km2, 4)
writer.addFeature(feature_out)
del writer

check = QgsVectorLayer(
    BOUNDARY_OUT + "|layername=watershed_boundary",
    "chk",
    "ogr",
)
crs_ok = check.crs().isValid()
check_authid = check.crs().authid()
del check
if not crs_ok:
    print("  boundary lacked CRS -> stamping", crs_authid)
    processing.run(
        "native:assignprojection",
        {
            "INPUT": BOUNDARY_OUT,
            "CRS": grid_crs,
            "OUTPUT": BOUNDARY_OUT,
        },
    )
    check_authid = crs_authid
print("watershed_boundary.gpkg written WITH CRS", check_authid)

# Save snapped outlet for inspection.
release_and_delete(SNAPPED_OUT)
snapped_fields = QgsFields()
snapped_fields.append(QgsField("id", QVariant.Int))
snapped_options = QgsVectorFileWriter.SaveVectorOptions()
snapped_options.driverName = "GPKG"
snapped_options.layerName = "outlet_snapped"
snapped_writer = QgsVectorFileWriter.create(
    SNAPPED_OUT,
    snapped_fields,
    QgsWkbTypes.Point,
    grid_crs,
    QgsCoordinateTransformContext(),
    snapped_options,
)
snapped_feature = QgsFeature(snapped_fields)
snapped_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
snapped_feature["id"] = 1
snapped_writer.addFeature(snapped_feature)
del snapped_writer
print("outlet_snapped.gpkg written.")

if ADD_TO_PROJECT:
    boundary_layer = QgsVectorLayer(
        BOUNDARY_OUT + "|layername=watershed_boundary",
        "watershed_boundary",
        "ogr",
    )
    if boundary_layer.isValid():
        project.addMapLayer(boundary_layer)

    snapped_layer = QgsVectorLayer(
        SNAPPED_OUT + "|layername=outlet_snapped",
        "outlet_snapped",
        "ogr",
    )
    if snapped_layer.isValid():
        project.addMapLayer(snapped_layer)

print("\nDone. Whole watershed -> watershed_boundary.gpkg (%.4f km2)." % area_km2)
print("Scratch rasters in temp/ (safe to delete).")

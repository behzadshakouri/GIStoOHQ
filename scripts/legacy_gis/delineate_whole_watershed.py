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
#   <SITE>/outputs/outlet.shp           single-feature outlet (operator-placed)
#   <SITE>/outputs/flow_dir.tif         from fillsink_etc.py
#   <SITE>/outputs/flow_acc.tif         from fillsink_etc.py (for snapping)
#
# OUTPUT
#   <SITE>/outputs/watershed_boundary.gpkg   layer 'watershed_boundary', id + area_km2
#   <SITE>/outputs/outlet_snapped.gpkg       snapped outlet (for inspection)
#   scratch rasters/gpkgs in <SITE>/outputs/temp/
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import processing
import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsFeature,
    QgsGeometry, QgsPointXY, QgsFields, QgsField, QgsVectorFileWriter,
    QgsWkbTypes, QgsCoordinateTransformContext, QgsCoordinateReferenceSystem
)
from qgis.PyQt.QtCore import QVariant

# --- root resolution -------------------------------------------------------
# Set ONCE per session in the console, BEFORE running any script:
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"      # Arash
#   ROOT = "C:/Users/smnfa/Dropbox/NHA/"           # Samaneh
#   SITE_DIR = "WS3_GIS/AZ12-100"
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

# --- settings --------------------------------------------------------------
OUTLET_REL  = "outlet.shp"        # single-feature outlet, in <SITE>/outputs/
FLOWDIR_REL = "flow_dir.tif"
FLOWACC_REL = "flow_acc.tif"

SNAP       = False
SNAP_CELLS = 2                    # half-window (cells) to snap onto highest |flow_acc|;
                                  # the single outlet can use a slightly larger window
                                  # than the interior pour points (no confluence risk)
FALLBACK_EPSG = 26912             # only if the flow-dir grid somehow lacks a CRS
ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
TEMP_DIR  = os.path.join(OUT_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

OUTLET_PATH  = os.path.join(OUT_DIR, OUTLET_REL)
FLOWDIR_PATH = os.path.join(OUT_DIR, FLOWDIR_REL)
FLOWACC_PATH = os.path.join(OUT_DIR, FLOWACC_REL)
BOUNDARY_OUT = os.path.join(OUT_DIR, "watershed_boundary.gpkg")
SNAPPED_OUT  = os.path.join(OUT_DIR, "outlet_snapped.gpkg")

print("Site       :", site_path)
print("Outlet     :", OUTLET_PATH)
print("Flow dir   :", FLOWDIR_PATH)
print("Boundary   :", BOUNDARY_OUT)

for p in (OUTLET_PATH, FLOWDIR_PATH, FLOWACC_PATH):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

def grass_id(name):
    from qgis.core import QgsApplication
    reg = QgsApplication.processingRegistry()
    for prefix in ("grass7:", "grass:"):
        if reg.algorithmById(prefix + name):
            return prefix + name
    return "grass7:" + name

fdir = QgsRasterLayer(FLOWDIR_PATH, "flow_dir")
if not fdir.isValid():
    raise Exception("Flow direction raster invalid: " + FLOWDIR_PATH)
grid_crs = fdir.crs()
if not grid_crs.isValid():
    grid_crs = QgsCoordinateReferenceSystem("EPSG:%d" % FALLBACK_EPSG)
    print("  flow_dir lacked CRS -> using EPSG:%d" % FALLBACK_EPSG)
crs_authid = grid_crs.authid() or ("EPSG:%d" % FALLBACK_EPSG)

pts = QgsVectorLayer(OUTLET_PATH, "outlet", "ogr")
if not pts.isValid():
    raise Exception("Outlet layer invalid: " + OUTLET_PATH)
feats = list(pts.getFeatures())
if len(feats) == 0:
    raise Exception("outlet.shp has no features.")
if len(feats) > 1:
    print("  NOTE: outlet.shp has %d features; using the FIRST one only." % len(feats))

feat = feats[0]
geom = feat.geometry()
if geom.isEmpty():
    raise Exception("outlet feature has empty geometry.")
pt = geom.asPoint()
x, y = pt.x(), pt.y()
print("\nOutlet point: %.2f, %.2f (%s)" % (x, y, pts.crs().authid()))

# --- snap to highest |flow_acc| within a small window ----------------------
if SNAP:
    ds = gdal.Open(FLOWACC_PATH)
    if ds is None:
        raise Exception("Could not open flow_acc: " + FLOWACC_PATH)
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    nx, ny = ds.RasterXSize, ds.RasterYSize
    mag = np.abs(band.ReadAsArray().astype("float64"))
    ds = None

    originX, pxW, _, originY, _, pxH = gt
    col = int((x - originX) / pxW)
    row = int((y - originY) / pxH)
    if 0 <= col < nx and 0 <= row < ny:
        c0, c1 = max(0, col - SNAP_CELLS), min(nx, col + SNAP_CELLS + 1)
        r0, r1 = max(0, row - SNAP_CELLS), min(ny, row + SNAP_CELLS + 1)
        sub = mag[r0:r1, c0:c1]
        ridx, cidx = np.unravel_index(int(np.argmax(sub)), sub.shape)
        sx = originX + (c0 + cidx + 0.5) * pxW
        sy = originY + (r0 + ridx + 0.5) * pxH
        moved = ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5
        print("Snap: moved %.1f m to highest |flow_acc| (window +/-%d cells)" % (moved, SNAP_CELLS))
        x, y = sx, sy
    else:
        print("Snap: outlet falls outside the grid; using as-is.")

# --- delineate: r.water.outlet -> polygonize -> select DN=1 -> dissolve -----
wat_ras = os.path.join(TEMP_DIR, "whole_wshed.tif")
wat_vec = os.path.join(TEMP_DIR, "whole_wshed.gpkg")

print("\nDelineating whole watershed at %.2f, %.2f ..." % (x, y))
processing.run(grass_id("r.water.outlet"), {
    "input": FLOWDIR_PATH, "coordinates": "%f,%f" % (x, y), "output": wat_ras,
    "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
    "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""})

# polygonize via the GDAL Python API directly. The gdal:polygonize processing
# wrapper silently fails on this install (returns success, writes nothing), so
# we call gdal.Polygonize() and write the GPKG via OGR.
for ext in ("", "-wal", "-shm", "-journal"):
    if os.path.exists(wat_vec + ext):
        try: os.remove(wat_vec + ext)
        except OSError: pass
_src = gdal.Open(wat_ras)
_band = _src.GetRasterBand(1)
_srs = osr.SpatialReference()
_srs.ImportFromWkt(_src.GetProjection())
_ds = ogr.GetDriverByName("GPKG").CreateDataSource(wat_vec)
_lyr = _ds.CreateLayer("whole_wshed", srs=_srs, geom_type=ogr.wkbPolygon)
_lyr.CreateField(ogr.FieldDefn("DN", ogr.OFTInteger))
gdal.Polygonize(_band, _band.GetMaskBand(), _lyr, 0, [], callback=None)
_ds = None
_src = None

poly_layer = QgsVectorLayer(wat_vec + "|layername=whole_wshed",
                            "whole_wshed_poly", "ogr")
if not poly_layer.isValid() or poly_layer.featureCount() == 0:
    raise Exception("gdal.Polygonize produced no polygons: " + wat_vec)

sel = processing.run("native:extractbyexpression", {
    "INPUT": poly_layer, "EXPRESSION": '"DN" = 1', "OUTPUT": "TEMPORARY_OUTPUT"})
diss = processing.run("native:dissolve", {
    "INPUT": sel["OUTPUT"], "FIELD": [], "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]

# --- collect dissolved geometry, write watershed_boundary.gpkg WITH CRS -----
dlyr = QgsVectorLayer(diss, "diss", "ogr") if isinstance(diss, str) else diss
geoms = [f.geometry() for f in dlyr.getFeatures() if not f.geometry().isEmpty()]
if not geoms:
    raise Exception("delineation produced no polygon (outlet may be off-channel).")
boundary = QgsGeometry.unaryUnion(geoms).makeValid()
area_km2 = boundary.area() / 1e6
print("Whole-watershed area: %.4f km2" % area_km2)

# release any loaded boundary layer + delete the file (Windows lock-safe)
proj = QgsProject.instance()
for lyr in list(proj.mapLayers().values()):
    if "watershed_boundary" in lyr.source().lower():
        proj.removeMapLayer(lyr.id())
if os.path.exists(BOUNDARY_OUT):
    deleted = False
    try:
        ok, _ = QgsVectorFileWriter.deleteSilently(BOUNDARY_OUT)
        deleted = ok
    except AttributeError:
        pass
    if not deleted:
        for ext in ("", "-wal", "-shm", "-journal"):
            p = BOUNDARY_OUT + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except PermissionError:
                    raise Exception("Cannot overwrite %s -- remove the "
                                    "'watershed_boundary' layer and re-run." % p)

fields = QgsFields()
fields.append(QgsField("id", QVariant.Int))
fields.append(QgsField("area_km2", QVariant.Double))
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName = "watershed_boundary"
bgeom = boundary
if bgeom.wkbType() not in (QgsWkbTypes.MultiPolygon, QgsWkbTypes.Polygon):
    coerced = bgeom.coerceToType(QgsWkbTypes.MultiPolygon)
    if coerced:
        bgeom = coerced[0]
writer = QgsVectorFileWriter.create(
    BOUNDARY_OUT, fields, QgsWkbTypes.MultiPolygon, grid_crs,
    QgsCoordinateTransformContext(), opts)
bf = QgsFeature(fields)
bf.setGeometry(bgeom)
bf["id"] = 1
bf["area_km2"] = round(area_km2, 4)
writer.addFeature(bf)
del writer

# verify CRS landed; force-stamp if not
chk = QgsVectorLayer(BOUNDARY_OUT + "|layername=watershed_boundary", "chk", "ogr")
crs_ok = chk.crs().isValid()
chk_authid = chk.crs().authid()
del chk
if not crs_ok:
    print("  boundary lacked CRS -> stamping", crs_authid)
    processing.run("native:assignprojection",
                   {"INPUT": BOUNDARY_OUT, "CRS": grid_crs, "OUTPUT": BOUNDARY_OUT})
    chk_authid = crs_authid
print("watershed_boundary.gpkg written WITH CRS", chk_authid)

# --- save the snapped outlet for inspection --------------------------------
sfields = QgsFields()
sfields.append(QgsField("id", QVariant.Int))
if os.path.exists(SNAPPED_OUT):
    try:
        QgsVectorFileWriter.deleteSilently(SNAPPED_OUT)
    except AttributeError:
        for ext in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(SNAPPED_OUT + ext):
                os.remove(SNAPPED_OUT + ext)
sopts = QgsVectorFileWriter.SaveVectorOptions()
sopts.driverName = "GPKG"
sopts.layerName = "outlet_snapped"
sw = QgsVectorFileWriter.create(SNAPPED_OUT, sfields, QgsWkbTypes.Point,
                                grid_crs, QgsCoordinateTransformContext(), sopts)
sf = QgsFeature(sfields)
sf.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
sf["id"] = 1
sw.addFeature(sf)
del sw
print("outlet_snapped.gpkg written.")

if ADD_TO_PROJECT:
    bl = QgsVectorLayer(BOUNDARY_OUT + "|layername=watershed_boundary",
                        "watershed_boundary", "ogr")
    if bl.isValid():
        proj.addMapLayer(bl)
    sl = QgsVectorLayer(SNAPPED_OUT + "|layername=outlet_snapped",
                        "outlet_snapped", "ogr")
    if sl.isValid():
        proj.addMapLayer(sl)

print("\nDone. Whole watershed -> watershed_boundary.gpkg (%.4f km2)." % area_km2)
print("Scratch rasters in temp/ (safe to delete).")

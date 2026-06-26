# =============================================================================
# Delineate one watershed per pour point, using a single flow-direction raster.
# Each watershed is saved as its own polygon (wshed_<id>_clean.gpkg).
#
# Built-in snap (pure Python, no addon): optionally moves each point to the
# highest flow-accumulation cell within a small window before delineating, so a
# point one cell off the thalweg still delineates the full channel. Keep the
# window SMALL (SNAP_CELLS = 1-2) so points near a confluence stay on their own
# branch instead of jumping to the main channel.
#
# Inputs from <SITE>/outputs/. ALL outputs to <SITE>/outputs/ (per-watershed
# scratch rasters/gpkgs go to <SITE>/outputs/temp/).
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
    QgsWkbTypes, QgsCoordinateTransformContext
)
from qgis.PyQt.QtCore import QVariant

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

# inputs, relative to <SITE>/outputs/ :
FLOWDIR_REL = "flow_dir.tif"
FLOWACC_REL = "flow_acc.tif"
POINTS_REL  = "pour_points.shp"          # if your points live elsewhere, give a
                                         # path relative to the SITE folder, e.g.
                                         # "demlr/pour_points.shp"

ID_FIELD   = None        # attribute to name outputs by; None -> feature order

SNAP       = True
SNAP_CELLS = 0           # half-window in cells; keep small (1-2) near confluences

ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

# --- derived paths ---------------------------------------------------------
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
TEMP_DIR  = os.path.join(OUT_DIR, "temp")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

FLOWDIR_PATH = os.path.join(OUT_DIR, FLOWDIR_REL)
FLOWACC_PATH = os.path.join(OUT_DIR, FLOWACC_REL)

# points: look in outputs/ first, then the site folder, then as given
def resolve_points(rel):
    cand = [os.path.join(OUT_DIR, rel),
            os.path.join(site_path, rel),
            os.path.join(ROOT, rel)]
    for p in cand:
        if os.path.exists(p):
            return p
    return cand[0]
POINTS_PATH = resolve_points(POINTS_REL)

print("Site        :", site_path)
print("Flow dir    :", FLOWDIR_PATH)
print("Pour points :", POINTS_PATH)
print("Outputs     :", OUT_DIR)

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

pts = QgsVectorLayer(POINTS_PATH, "pour_points", "ogr")
if not pts.isValid():
    raise Exception("Pour points layer invalid: " + POINTS_PATH)
print("Points      :", pts.featureCount())

# --- pure-Python snap to highest accumulation cell -------------------------
snap_data = None
if SNAP:
    ds = gdal.Open(FLOWACC_PATH)
    if ds is None:
        raise Exception("Could not open flow_acc: " + FLOWACC_PATH)
    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    nx, ny = ds.RasterXSize, ds.RasterYSize
    mag = np.abs(band.ReadAsArray().astype("float64"))
    snap_data = (gt, nx, ny, mag)
    print("Snap: ON (window +/-%d cells, highest |flow_acc|)" % SNAP_CELLS)

def snap_point(x, y):
    gt, nx, ny, mag = snap_data
    originX, pxW, _, originY, _, pxH = gt
    col = int((x - originX) / pxW)
    row = int((y - originY) / pxH)
    if col < 0 or col >= nx or row < 0 or row >= ny:
        return x, y
    c0, c1 = max(0, col - SNAP_CELLS), min(nx, col + SNAP_CELLS + 1)
    r0, r1 = max(0, row - SNAP_CELLS), min(ny, row + SNAP_CELLS + 1)
    sub = mag[r0:r1, c0:c1]
    ridx, cidx = np.unravel_index(int(np.argmax(sub)), sub.shape)
    best_col, best_row = c0 + cidx, r0 + ridx
    sx = originX + (best_col + 0.5) * pxW
    sy = originY + (best_row + 0.5) * pxH
    return sx, sy

# --- iterate points, (snap), delineate, polygonize -------------------------
print("\nDelineating one watershed per point...")
made = []
field_names = [f.name() for f in pts.fields()]
for i, feat in enumerate(pts.getFeatures(), start=1):
    geom = feat.geometry()
    if geom.isEmpty():
        print(f"  point {i}: empty geometry, skipped")
        continue
    pt = geom.asPoint()
    x, y = pt.x(), pt.y()

    if SNAP:
        sx, sy = snap_point(x, y)
        moved = ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5
        print(f"  point {i}: snapped {moved:.1f} m")
        x, y = sx, sy

    if ID_FIELD and ID_FIELD in field_names:
        tag = str(feat[ID_FIELD])
    else:
        tag = str(i)
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in tag)

    # scratch rasters/gpkgs go to temp/, final cleaned polygon to outputs/
    wat_ras = os.path.join(TEMP_DIR, f"wshed_{safe}.tif")
    wat_vec = os.path.join(TEMP_DIR, f"wshed_{safe}.gpkg")
    cleaned = os.path.join(OUT_DIR,  f"wshed_{safe}_clean.gpkg")
    print(f"  [{i}/{pts.featureCount()}] id='{tag}' at {x:.2f},{y:.2f}")

    processing.run(grass_id("r.water.outlet"), {
        "input": FLOWDIR_PATH, "coordinates": f"{x},{y}", "output": wat_ras,
        "GRASS_REGION_PARAMETER": None, "GRASS_REGION_CELLSIZE_PARAMETER": 0,
        "GRASS_RASTER_FORMAT_OPT": "", "GRASS_RASTER_FORMAT_META": ""})

    # polygonize via the GDAL Python API directly. The gdal:polygonize
    # processing wrapper silently fails on some installs (returns success,
    # writes nothing), so we call gdal.Polygonize() and write the GPKG via OGR.
    for ext in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(wat_vec + ext):
            try: os.remove(wat_vec + ext)
            except OSError: pass
    _src = gdal.Open(wat_ras)
    _band = _src.GetRasterBand(1)
    _srs = osr.SpatialReference()
    _srs.ImportFromWkt(_src.GetProjection())
    _ds = ogr.GetDriverByName("GPKG").CreateDataSource(wat_vec)
    _lyr = _ds.CreateLayer("wshed", srs=_srs, geom_type=ogr.wkbPolygon)
    _lyr.CreateField(ogr.FieldDefn("DN", ogr.OFTInteger))
    gdal.Polygonize(_band, _band.GetMaskBand(), _lyr, 0, [], callback=None)
    _ds = None
    _src = None

    poly_layer = QgsVectorLayer(wat_vec + "|layername=wshed",
                                f"wshed_{safe}_poly", "ogr")
    if not poly_layer.isValid() or poly_layer.featureCount() == 0:
        print(f"      WARNING: polygonize produced no polygons for id='{tag}', skipping")
        continue

    sel = processing.run("native:extractbyexpression", {
        "INPUT": poly_layer, "EXPRESSION": '"DN" = 1', "OUTPUT": "TEMPORARY_OUTPUT"})
    diss = processing.run("native:dissolve", {
        "INPUT": sel["OUTPUT"], "FIELD": [], "OUTPUT": "TEMPORARY_OUTPUT"})
    processing.run("native:fieldcalculator", {
        "INPUT": diss["OUTPUT"], "FIELD_NAME": "area_km2", "FIELD_TYPE": 0,
        "FIELD_LENGTH": 12, "FIELD_PRECISION": 4,
        "FORMULA": "$area / 1000000", "OUTPUT": cleaned})

    made.append((tag, cleaned))
    print(f"      -> {cleaned}")

# --- save snapped points so you can see where they moved -------------------
if SNAP and made:
    snapped_path = os.path.join(OUT_DIR, "pour_points_snapped.gpkg")
    fields = QgsFields()
    fields.append(QgsField("id", QVariant.String))
    if os.path.exists(snapped_path):
        try:
            QgsVectorFileWriter.deleteSilently(snapped_path)
        except AttributeError:
            for ext in ("", "-wal", "-shm", "-journal"):
                if os.path.exists(snapped_path + ext):
                    os.remove(snapped_path + ext)
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"; opts.layerName = "pour_points_snapped"
    w = QgsVectorFileWriter.create(snapped_path, fields, QgsWkbTypes.Point,
                                   pts.crs(), QgsCoordinateTransformContext(), opts)
    for i, feat in enumerate(pts.getFeatures(), start=1):
        pt = feat.geometry().asPoint()
        sx, sy = snap_point(pt.x(), pt.y())
        f2 = QgsFeature(fields)
        f2.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(sx, sy)))
        f2["id"] = str(i)
        w.addFeature(f2)
    del w
    print("\nSnapped points ->", snapped_path)

if ADD_TO_PROJECT:
    proj = QgsProject.instance()
    for tag, path in made:
        lyr = QgsVectorLayer(path, f"wshed_{tag}", "ogr")
        if lyr.isValid():
            proj.addMapLayer(lyr)

print(f"\nDone. {len(made)} watershed(s) in {OUT_DIR}")
print("Scratch rasters are in temp/ (safe to delete).")
print("If a watershed does not climb the channel after snapping, the channel")
print("is not continuously downhill there -- in fillsink_etc.py use")
print("BURN_MODE='synthetic' (guarantees descent), regenerate flow_dir, re-run.")

# =============================================================================
# ras_cut_lines.py   (QGIS Python Console)
#
# HEC-RAS PIPELINE -- STEP 2a: generate 1D cross-section CUT LINES along the
# reach centerline. Perpendicular to a SMOOTHED flow direction (so adjacent
# sections do not fan out and cross on bends), spaced every SPACING_M, each
# extending HALF_LEN_M to either side. Validates ordering + crossings and writes
# the cut lines as a GeoPackage plus a plan-view CSV for plotting.
#
# This is plan-view geometry ONLY. Station-elevation profiles (the actual
# cross-section shapes RAS uses) are sampled from the DEM in the NEXT sub-step.
#
# INPUT  (from step 1, in <SITE>/outputs_RAS/)
#   reach_centerline.gpkg   single main-stem reach (upstream -> downstream)
#   ras_terrain.tif         1 m DEM clipped to the corridor bbox (extent check)
#
# OUTPUT (in <SITE>/outputs_RAS/)
#   cut_lines.gpkg          one line per cross section (xs_id, station_m, ...)
#   cut_lines_plan.csv      vertices of every cut line for plotting:
#                           xs_id, station_m, vertex_i, x, y, offset_m
#
# Run from: QGIS -> Plugins -> Python Console.
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   exec(open(SCRIPT_DIR + "/ras_cut_lines.py").read())
# =============================================================================
import os
import csv
import math
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsFeature,
    QgsGeometry, QgsPointXY, QgsField, QgsFields,
    QgsCoordinateReferenceSystem, QgsVectorFileWriter, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant

# --- root resolution -------------------------------------------------------
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

# --- settings --------------------------------------------------------------
CENTERLINE_NAME = "reach_centerline.gpkg"
TERRAIN_NAME    = "ras_terrain.tif"

SPACING_M       = 50.0       # distance between cross sections along the reach
HALF_LEN_M      = 200.0      # cut-line half-length each side of the centerline
SMOOTH_STATIONS = 1          # flow dir averaged over +/- this many stations
                             #   (1 -> +/-50 m window). Raise on sinuous reaches.
TRIM_AT_FIRST   = True       # endpoints: keep stations strictly inside the reach
FALLBACK_EPSG   = 26912
ADD_TO_PROJECT  = True

CUTLINES_NAME   = "cut_lines.gpkg"
CSV_NAME        = "cut_lines_plan.csv"
# ---------------------------------------------------------------------------

project   = QgsProject.instance()
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs_RAS")

CL_PATH   = os.path.join(OUT_DIR, CENTERLINE_NAME)
DEM_PATH  = os.path.join(OUT_DIR, TERRAIN_NAME)
CUT_PATH  = os.path.join(OUT_DIR, CUTLINES_NAME)
CSV_PATH  = os.path.join(OUT_DIR, CSV_NAME)

print("=" * 70)
print("HEC-RAS STEP 2a -- cross-section cut lines")
print("  spacing   : %.0f m" % SPACING_M)
print("  half-len  : %.0f m" % HALF_LEN_M)
print("  smoothing : +/- %d station(s)" % SMOOTH_STATIONS)
print("=" * 70)

# --- preflight -------------------------------------------------------------
for p in (CL_PATH, DEM_PATH):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

cl_layer = QgsVectorLayer(CL_PATH, "centerline", "ogr")
if not cl_layer.isValid():
    raise Exception("centerline invalid: " + CL_PATH)
crs = cl_layer.crs() if cl_layer.crs().isValid() \
    else QgsCoordinateReferenceSystem("EPSG:%d" % FALLBACK_EPSG)

cl_feat = next(cl_layer.getFeatures(), None)
if cl_feat is None:
    raise Exception("centerline layer is empty.")
geom = cl_feat.geometry()
pts = geom.asMultiPolyline()[0] if geom.isMultipart() else geom.asPolyline()
if len(pts) < 2:
    raise Exception("centerline has < 2 vertices.")
P = [(p.x(), p.y()) for p in pts]
total_len = geom.length()
print("centerline length: %.0f m | vertices: %d" % (total_len, len(P)))

# --- helper: point + tangent at a given distance along the polyline --------
# Precompute cumulative distance at each vertex.
cum = [0.0]
for i in range(len(P) - 1):
    cum.append(cum[-1] + math.hypot(P[i+1][0]-P[i][0], P[i+1][1]-P[i][1]))

def point_at(s):
    """(x, y) at distance s along the line (clamped to the ends)."""
    if s <= 0:
        return P[0]
    if s >= cum[-1]:
        return P[-1]
    # find segment containing s
    lo, hi = 0, len(cum) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    seg = cum[lo+1] - cum[lo]
    t = 0.0 if seg == 0 else (s - cum[lo]) / seg
    return (P[lo][0] + t*(P[lo+1][0]-P[lo][0]),
            P[lo][1] + t*(P[lo+1][1]-P[lo][1]))

def tangent_at(s, window):
    """Smoothed unit flow direction at station s: vector from (s-window) to
       (s+window) along the line. Falls back to a shorter span at the ends."""
    a = point_at(max(s - window, 0.0))
    b = point_at(min(s + window, cum[-1]))
    dx, dy = (b[0]-a[0]), (b[1]-a[1])
    n = math.hypot(dx, dy)
    if n < 1e-9:
        # degenerate (window collapsed): use the immediate segment direction
        a = point_at(max(s - 1.0, 0.0)); b = point_at(min(s + 1.0, cum[-1]))
        dx, dy = (b[0]-a[0]), (b[1]-a[1]); n = math.hypot(dx, dy) or 1.0
    return dx/n, dy/n

# --- build stations --------------------------------------------------------
window = SMOOTH_STATIONS * SPACING_M
stations = []
s = 0.0
while s <= total_len + 1e-6:
    stations.append(s)
    s += SPACING_M
if TRIM_AT_FIRST:
    # drop the exact endpoints so cut lines sit strictly inside the reach
    stations = [s for s in stations if 1e-6 < s < total_len - 1e-6]
print("cross-section stations: %d" % len(stations))

# --- generate cut lines ----------------------------------------------------
# RAS convention: river-station increases going UPSTREAM. The centerline runs
# upstream->downstream (built that way in step 1), so station 0 = upstream end.
# We give each XS a river_sta in feet from the downstream end (so the most
# downstream XS has the smallest station), matching RAS's increasing-upstream
# convention; ordering is recorded explicitly too.
cut_records = []   # (order_idx, station_m, left_xy, mid_xy, right_xy)
for idx, s in enumerate(stations):
    mx, my = point_at(s)
    tx, ty = tangent_at(s, window)
    # left-normal = rotate tangent +90 deg; right-normal = -90 deg
    nx, ny = -ty, tx
    left  = (mx + nx * HALF_LEN_M, my + ny * HALF_LEN_M)
    right = (mx - nx * HALF_LEN_M, my - ny * HALF_LEN_M)
    cut_records.append((idx, s, left, (mx, my), right))

# --- validate: adjacent cut-line crossings ---------------------------------
def seg_cross(p1, p2, p3, p4):
    """True if segment p1p2 intersects p3p4 (proper or touching)."""
    def ccw(a, b, c):
        return (c[1]-a[1])*(b[0]-a[0]) - (b[1]-a[1])*(c[0]-a[0])
    d1 = ccw(p3, p4, p1); d2 = ccw(p3, p4, p2)
    d3 = ccw(p1, p2, p3); d4 = ccw(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False

crossings = 0
for i in range(len(cut_records) - 1):
    _, _, l1, _, r1 = cut_records[i]
    _, _, l2, _, r2 = cut_records[i+1]
    if seg_cross(l1, r1, l2, r2):
        crossings += 1
        print("    !! cut lines %d and %d cross (tighten on a bend)" % (i, i+1))
if crossings:
    print("    %d crossing(s) found -- raise SMOOTH_STATIONS or shorten HALF_LEN_M,"
          % crossings)
    print("       or hand-edit those sections before sampling elevations.")
else:
    print("    no adjacent cut-line crossings.")

# --- write cut_lines.gpkg --------------------------------------------------
flds = QgsFields()
flds.append(QgsField("xs_id",     QVariant.Int))
flds.append(QgsField("station_m", QVariant.Double))   # along reach from upstream end
flds.append(QgsField("river_sta", QVariant.Double))   # ft from downstream end (RAS)
flds.append(QgsField("len_m",     QVariant.Double))
opt = QgsVectorFileWriter.SaveVectorOptions(); opt.driverName = "GPKG"
w = QgsVectorFileWriter.create(CUT_PATH, flds, QgsWkbTypes.LineString,
                               crs, project.transformContext(), opt)
M_TO_FT = 3.280839895
for (idx, s, left, mid, right) in cut_records:
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPolylineXY(
        [QgsPointXY(*left), QgsPointXY(*mid), QgsPointXY(*right)]))
    river_sta_ft = (total_len - s) * M_TO_FT     # downstream end = 0, increases upstream
    ft.setAttribute("xs_id", int(idx))
    ft.setAttribute("station_m", float(s))
    ft.setAttribute("river_sta", float(river_sta_ft))
    ft.setAttribute("len_m", float(2.0 * HALF_LEN_M))
    w.addFeature(ft)
del w
print("wrote", CUT_PATH)

# --- write plan-view CSV ---------------------------------------------------
with open(CSV_PATH, "w", newline="") as fh:
    wr = csv.writer(fh)
    wr.writerow(["xs_id", "station_m", "vertex_i", "x", "y", "offset_m"])
    for (idx, s, left, mid, right) in cut_records:
        # offset_m: signed distance along the cut line, left = -HALF_LEN, 0 at
        # centerline, right = +HALF_LEN (handy for plotting against elevation)
        for vi, (pt, off) in enumerate(
                [(left, -HALF_LEN_M), (mid, 0.0), (right, +HALF_LEN_M)]):
            wr.writerow([idx, "%.3f" % s, vi,
                         "%.3f" % pt[0], "%.3f" % pt[1], "%.3f" % off])
print("wrote", CSV_PATH)

if ADD_TO_PROJECT:
    lyr = QgsVectorLayer(CUT_PATH, "cut_lines", "ogr")
    if lyr.isValid():
        project.addMapLayer(lyr)

print("\n" + "=" * 70)
print("STEP 2a COMPLETE.")
print("=" * 70)
print("Cut lines :", CUT_PATH, "(%d sections)" % len(cut_records))
print("Plan CSV  :", CSV_PATH)
print("\nVERIFY before sampling elevations:")
print("  - cut lines are roughly perpendicular to the channel, no crossings")
print("  - they span the floodplain (widen HALF_LEN_M if the flood escapes them)")
print("  - spacing resolves the channel (tighten SPACING_M on rapidly-varying reaches)")
print("\nNEXT: step 2b -- sample station-elevation along each cut line from the")
print("1 m DEM -> the cross-section profiles (a second CSV: xs_id, offset_m, elev_m).")
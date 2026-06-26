# =============================================================================
# ras_thalweg_recenter.py   (QGIS Python Console)
#
# HEC-RAS PIPELINE -- STEP 2c: post-process the cut lines so each cross section
# is centered on the MAIN-STEM THALWEG (not the NHD line, which can sit up on a
# bank), then rebuild the centerline + cut lines around the corrected points.
# Flagged confluence sections (e.g. XS 10, where the cut line straddles the
# tributary) are NOT trusted: their geometry is linearly INTERPOLATED between
# the nearest good sections upstream and downstream.
#
# WHY: cross sections 0/1/2 had the channel low ~30-50 m off the NHD centerline;
# XS 10 spans the tributary confluence. Re-centering fixes the first; flag +
# interpolate fixes the second without trimming (so parallel flood channels,
# which we WANT in the section, are never removed).
#
# INPUT  (from steps 1-2, in <SITE>/outputs_RAS/)
#   cut_lines.gpkg        cut lines (xs_id, station_m, river_sta)   -- step 2a
#   reach_centerline.gpkg main-stem centerline                      -- step 1
#   ras_terrain.tif       1 m DEM                                   -- step 1
#
# OUTPUT (in <SITE>/outputs_RAS/)
#   cut_lines_recentered.gpkg     corrected cut lines (status: ok|interp)
#   reach_centerline_thalweg.gpkg corrected (thalweg) centerline
#   xs_profiles_recentered.csv    RAW sampled profiles + 'status' column
#   xs_profiles_model.gp.dat      MODEL profiles (interpolated where flagged),
#                                 gnuplot block format for plotting/checking
#
# SETTINGS of note:
#   THALWEG_WINDOW_M  +/- search window for the channel low (50 m: catches the
#                     offset thalweg, ignores a tributary 100+ m away).
#   INTERPOLATE_XS    list of xs_id to interpolate instead of trust (e.g. [10]).
#
# Run from: QGIS -> Plugins -> Python Console.
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   exec(open(SCRIPT_DIR + "/ras_thalweg_recenter.py").read())
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
CUTLINES_NAME   = "cut_lines.gpkg"
TERRAIN_NAME    = "ras_terrain.tif"

THALWEG_WINDOW_M = 50.0       # +/- search window around offset 0 for the thalweg
HALF_LEN_M       = 200.0      # cut-line half-length (match step 2a)
SAMPLE_STEP_M    = 1.0        # along-cut-line sampling (match step 2b)
SMOOTH_STATIONS  = 1          # re-perpendicular smoothing window (match step 2a)
INTERPOLATE_XS   = [10]       # xs_id(s) to interpolate instead of trust
NODATA_DROP      = True
FALLBACK_EPSG    = 26912
ADD_TO_PROJECT   = True

CUT_OUT_NAME    = "cut_lines_recentered.gpkg"
CL_OUT_NAME     = "reach_centerline_thalweg.gpkg"
CSV_OUT_NAME    = "xs_profiles_recentered.csv"
DAT_OUT_NAME    = "xs_profiles_model.gp.dat"
THAL_OUT_NAME   = "thalweg_points.gpkg"          # per-XS thalweg + shift record
MODEL_CSV_NAME  = "xs_profiles_model.csv"        # model profiles WITH (x,y)
# ---------------------------------------------------------------------------

project   = QgsProject.instance()
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs_RAS")

CUT_PATH  = os.path.join(OUT_DIR, CUTLINES_NAME)
DEM_PATH  = os.path.join(OUT_DIR, TERRAIN_NAME)
CUT_OUT   = os.path.join(OUT_DIR, CUT_OUT_NAME)
CL_OUT    = os.path.join(OUT_DIR, CL_OUT_NAME)
CSV_OUT   = os.path.join(OUT_DIR, CSV_OUT_NAME)
DAT_OUT   = os.path.join(OUT_DIR, DAT_OUT_NAME)
THAL_OUT  = os.path.join(OUT_DIR, THAL_OUT_NAME)
MODEL_CSV = os.path.join(OUT_DIR, MODEL_CSV_NAME)

print("=" * 70)
print("HEC-RAS STEP 2c -- thalweg re-centering + confluence interpolation")
print("  thalweg window :", THALWEG_WINDOW_M, "m")
print("  interpolate XS :", INTERPOLATE_XS)
print("=" * 70)

for p in (CUT_PATH, DEM_PATH):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

cuts = QgsVectorLayer(CUT_PATH, "cut_lines", "ogr")
if not cuts.isValid():
    raise Exception("cut lines invalid: " + CUT_PATH)
crs = cuts.crs() if cuts.crs().isValid() \
    else QgsCoordinateReferenceSystem("EPSG:%d" % FALLBACK_EPSG)
dem = QgsRasterLayer(DEM_PATH, "ras_terrain")
if not dem.isValid():
    raise Exception("DEM invalid: " + DEM_PATH)
prov = dem.dataProvider()

def sample(x, y):
    val, ok = prov.sample(QgsPointXY(x, y), 1)
    if not ok or val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or val < -9000):
        return None
    return float(val)

# --- read cut lines, sample profile, find thalweg within window ------------
# For each XS we store: id, station, river_sta, mid (x,y), unit normal (nx,ny),
# the sampled profile [(offset, x, y, z)], and the chosen thalweg offset.
INTERP = set(int(i) for i in INTERPOLATE_XS)
sections = []
for f in sorted(cuts.getFeatures(), key=lambda ff: ff["xs_id"]):
    xs_id  = int(f["xs_id"])
    sta_m  = float(f["station_m"])
    riv_ft = float(f["river_sta"])
    g = f.geometry()
    line = g.asMultiPolyline()[0] if g.isMultipart() else g.asPolyline()
    if len(line) < 2:
        continue
    pa, pb = line[0], line[-1]                 # left, right
    seglen = math.hypot(pb.x()-pa.x(), pb.y()-pa.y())
    ux, uy = (pb.x()-pa.x())/seglen, (pb.y()-pa.y())/seglen   # left->right unit
    half = seglen / 2.0
    mid = (pa.x()+ux*half, pa.y()+uy*half)     # geometric center == old centerline
    # sample the profile
    prof = []
    d = 0.0
    while d <= seglen + 1e-9:
        x = pa.x()+ux*d; y = pa.y()+uy*d
        z = sample(x, y)
        prof.append((d-half, x, y, z))         # offset (left -, right +)
        d += SAMPLE_STEP_M
    # thalweg = lowest valid sample within +/- THALWEG_WINDOW_M of offset 0
    best_off, best_z = None, None
    for (off, x, y, z) in prof:
        if z is None or abs(off) > THALWEG_WINDOW_M:
            continue
        if best_z is None or z < best_z:
            best_z, best_off = z, off
    if best_off is None:
        best_off = 0.0                          # no valid point in window: keep center
    # thalweg ground point
    tx = mid[0] + ux * best_off
    ty = mid[1] + uy * best_off
    sections.append({
        "id": xs_id, "sta": sta_m, "riv": riv_ft,
        "mid": mid, "u": (ux, uy), "prof": prof,
        "old_center_xy": mid,                   # pre-shift NHD-based center
        "thalweg_off": best_off, "thalweg_xy": (tx, ty),
        "status": "interp" if xs_id in INTERP else "ok",
    })

print("sections read: %d (flagged interp: %d)"
      % (len(sections), sum(1 for s in sections if s["status"] == "interp")))

# --- build corrected centerline through thalweg points ---------------------
# Order by station ascending (upstream end first, as in step 1).
sections.sort(key=lambda s: s["sta"])
thal_pts = [s["thalweg_xy"] for s in sections]
cl_geom = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in thal_pts])

# --- recompute a SMOOTHED perpendicular at each thalweg point ---------------
# Using the corrected centerline so cut lines are perpendicular to the real
# channel path, not the old NHD line.
def tangent(idx):
    w = max(1, SMOOTH_STATIONS)
    a = sections[max(idx - w, 0)]["thalweg_xy"]
    b = sections[min(idx + w, len(sections) - 1)]["thalweg_xy"]
    dx, dy = b[0]-a[0], b[1]-a[1]
    n = math.hypot(dx, dy) or 1.0
    return dx/n, dy/n

# --- RE-SAMPLE each section along its REBUILT, thalweg-centered cut line ----
# The original sec["prof"] is sampled about the OLD NHD center, so its offsets
# put 0 on the old line, not the thalweg. We resample the DEM along the new cut
# line (perpendicular to the corrected centerline, centered on the thalweg) so
# offset 0 lands on the channel low, and the (x, y) lie on the actual new line.
for idx, sec in enumerate(sections):
    cx, cy = sec["thalweg_xy"]
    tx, ty = tangent(idx)
    nx, ny = -ty, tx                        # left normal of the corrected line
    rprof = []
    off = -HALF_LEN_M
    while off <= HALF_LEN_M + 1e-9:
        x = cx + nx * off
        y = cy + ny * off
        z = sample(x, y)
        rprof.append((off, x, y, z))
        off += SAMPLE_STEP_M
    sec["rprof"] = rprof                    # thalweg-centered profile

# --- helpers for the interpolation of flagged sections ---------------------
def profile_dict(sec):
    """offset(rounded) -> elev, for valid samples on the RECENTERED profile."""
    out = {}
    for (off, x, y, z) in sec["rprof"]:
        if z is not None:
            out[round(off)] = z
    return out

def nearest_ok(idx, direction):
    j = idx + direction
    while 0 <= j < len(sections):
        if sections[j]["status"] == "ok":
            return j
        j += direction
    return None

# --- assemble outputs ------------------------------------------------------
# For each section, the MODEL profile is either the recentered sampled one (ok)
# or the offset-matched linear interpolation of its nearest ok neighbors
# (interp). Every model point carries (x, y) for later inundation mapping; for
# interpolated sections the ELEVATION is blended from neighbors but the (x, y)
# come from THIS section's own recentered cut line.
def xy_at_offset(idx, off):
    """Map an offset along section idx's recentered cut line to (x, y)."""
    cx, cy = sections[idx]["thalweg_xy"]
    tx, ty = tangent(idx)
    nx, ny = -ty, tx
    return (cx + nx * off, cy + ny * off)

model_profiles = {}   # xs_id -> list of (offset, x, y, elev)
for idx, sec in enumerate(sections):
    if sec["status"] == "ok":
        model_profiles[sec["id"]] = [(off, x, y, z)
                                     for (off, x, y, z) in sec["rprof"]
                                     if z is not None]
        continue
    # interpolate ELEV between nearest ok up/down neighbors, matched by offset;
    # take (x, y) from THIS section's own cut line at that offset.
    iu = nearest_ok(idx, -1)
    idn = nearest_ok(idx, +1)
    if iu is None or idn is None:
        print("    !! XS %d flagged interp but missing an ok neighbor on one "
              "side; using raw profile." % sec["id"])
        model_profiles[sec["id"]] = [(off, x, y, z)
                                     for (off, x, y, z) in sec["rprof"]
                                     if z is not None]
        continue
    du = profile_dict(sections[iu]); dd = profile_dict(sections[idn])
    sta_u = sections[iu]["sta"]; sta_d = sections[idn]["sta"]
    span = (sta_d - sta_u) or 1.0
    w_d = (sec["sta"] - sta_u) / span           # weight toward downstream nbr
    w_u = 1.0 - w_d
    offsets = sorted(set(du.keys()) & set(dd.keys()))
    rows = []
    for o in offsets:
        z = w_u*du[o] + w_d*dd[o]
        x, y = xy_at_offset(idx, float(o))      # own geometry, not neighbors'
        rows.append((float(o), x, y, z))
    model_profiles[sec["id"]] = rows
    print("    XS %d interpolated from XS %d / XS %d (w_up=%.2f, w_dn=%.2f)"
          % (sec["id"], sections[iu]["id"], sections[idn]["id"], w_u, w_d))

# --- write corrected centerline --------------------------------------------
optC = QgsVectorFileWriter.SaveVectorOptions(); optC.driverName = "GPKG"
fldsC = QgsFields(); fldsC.append(QgsField("len_m", QVariant.Double))
wC = QgsVectorFileWriter.create(CL_OUT, fldsC, QgsWkbTypes.LineString,
                                crs, project.transformContext(), optC)
fC = QgsFeature(fldsC); fC.setGeometry(cl_geom)
fC.setAttribute("len_m", float(cl_geom.length()))
wC.addFeature(fC); del wC
print("wrote", CL_OUT)

# --- write recentered cut lines --------------------------------------------
fldsK = QgsFields()
fldsK.append(QgsField("xs_id", QVariant.Int))
fldsK.append(QgsField("station_m", QVariant.Double))
fldsK.append(QgsField("river_sta", QVariant.Double))
fldsK.append(QgsField("thalweg_off", QVariant.Double))
fldsK.append(QgsField("status", QVariant.String))
optK = QgsVectorFileWriter.SaveVectorOptions(); optK.driverName = "GPKG"
wK = QgsVectorFileWriter.create(CUT_OUT, fldsK, QgsWkbTypes.LineString,
                                crs, project.transformContext(), optK)
for idx, sec in enumerate(sections):
    cx, cy = sec["thalweg_xy"]
    tx, ty = tangent(idx)
    nx, ny = -ty, tx                            # left normal
    left  = (cx + nx*HALF_LEN_M, cy + ny*HALF_LEN_M)
    right = (cx - nx*HALF_LEN_M, cy - ny*HALF_LEN_M)
    ft = QgsFeature(fldsK)
    ft.setGeometry(QgsGeometry.fromPolylineXY(
        [QgsPointXY(*left), QgsPointXY(cx, cy), QgsPointXY(*right)]))
    ft.setAttribute("xs_id", sec["id"])
    ft.setAttribute("station_m", sec["sta"])
    ft.setAttribute("river_sta", sec["riv"])
    ft.setAttribute("thalweg_off", sec["thalweg_off"])
    ft.setAttribute("status", sec["status"])
    wK.addFeature(ft)
del wK
print("wrote", CUT_OUT)

# --- write RAW recentered profiles CSV (with status) -----------------------
# "recentered" = sampled along the rebuilt thalweg-centered cut line, so offset
# 0 is the channel low. (For flagged sections this is the real sampled shape,
# i.e. XS 10 still shows its true confluence here; the model file is the one
# that substitutes the interpolated version.)
with open(CSV_OUT, "w", newline="") as fh:
    wr = csv.writer(fh)
    wr.writerow(["xs_id", "station_m", "river_sta_ft",
                 "offset_m", "x", "y", "elev_m", "status"])
    for sec in sections:
        for (off, x, y, z) in sec["rprof"]:
            if z is None and NODATA_DROP:
                continue
            wr.writerow([sec["id"], "%.3f" % sec["sta"], "%.3f" % sec["riv"],
                         "%.3f" % off, "%.3f" % x, "%.3f" % y,
                         "" if z is None else "%.3f" % z, sec["status"]])
print("wrote", CSV_OUT)

# --- write MODEL profiles (interpolated where flagged) as gnuplot blocks ----
with open(DAT_OUT, "w", newline="") as fh:
    fh.write("# HEC-RAS MODEL cross-section profiles (interp where flagged)\n")
    fh.write("# columns: offset_m  elev_m\n")
    first = True
    for sec in sections:
        if not first:
            fh.write("\n\n")
        first = False
        fh.write("# xs_id=%d  station_m=%.3f  river_sta_ft=%.1f  status=%s\n"
                 % (sec["id"], sec["sta"], sec["riv"], sec["status"]))
        for (off, x, y, z) in model_profiles[sec["id"]]:
            fh.write("%.3f %.3f\n" % (off, z))
print("wrote", DAT_OUT)

# --- write MODEL profiles CSV WITH (x, y) ----------------------------------
# Long format with coordinates for EVERY model point -- this is what the later
# inundation-mapping step consumes (WSE at each XS -> wetted extent uses these
# x, y). Interpolated sections carry their OWN cut-line (x, y) here, not the
# neighbors', so the WSE is drawn at the section's true plan-view location.
with open(MODEL_CSV, "w", newline="") as fh:
    wr = csv.writer(fh)
    wr.writerow(["xs_id", "station_m", "river_sta_ft",
                 "offset_m", "x", "y", "elev_m", "status"])
    for sec in sections:
        for (off, x, y, z) in model_profiles[sec["id"]]:
            wr.writerow([sec["id"], "%.3f" % sec["sta"], "%.3f" % sec["riv"],
                         "%.3f" % off, "%.3f" % x, "%.3f" % y,
                         "%.3f" % z, sec["status"]])
print("wrote", MODEL_CSV)

# --- write THALWEG POINTS layer (coords + shift record) --------------------
# One point per XS at the corrected channel low, carrying the pre-shift NHD
# center and the shift, so the re-centering is fully auditable/reversible and
# the thalweg coordinates are available to the inundation step.
fldsT = QgsFields()
fldsT.append(QgsField("xs_id", QVariant.Int))
fldsT.append(QgsField("river_sta", QVariant.Double))
fldsT.append(QgsField("thalweg_x", QVariant.Double))
fldsT.append(QgsField("thalweg_y", QVariant.Double))
fldsT.append(QgsField("old_cx", QVariant.Double))
fldsT.append(QgsField("old_cy", QVariant.Double))
fldsT.append(QgsField("shift_off_m", QVariant.Double))
fldsT.append(QgsField("status", QVariant.String))
optT = QgsVectorFileWriter.SaveVectorOptions(); optT.driverName = "GPKG"
wT = QgsVectorFileWriter.create(THAL_OUT, fldsT, QgsWkbTypes.Point,
                                crs, project.transformContext(), optT)
for sec in sections:
    tx, ty = sec["thalweg_xy"]; ocx, ocy = sec["old_center_xy"]
    ft = QgsFeature(fldsT)
    ft.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(tx, ty)))
    ft.setAttribute("xs_id", sec["id"])
    ft.setAttribute("river_sta", sec["riv"])
    ft.setAttribute("thalweg_x", float(tx)); ft.setAttribute("thalweg_y", float(ty))
    ft.setAttribute("old_cx", float(ocx)); ft.setAttribute("old_cy", float(ocy))
    ft.setAttribute("shift_off_m", float(sec["thalweg_off"]))
    ft.setAttribute("status", sec["status"])
    wT.addFeature(ft)
del wT
print("wrote", THAL_OUT)

if ADD_TO_PROJECT:
    for p, nm in [(CUT_OUT, "cut_lines_recentered"),
                  (CL_OUT, "reach_centerline_thalweg"),
                  (THAL_OUT, "thalweg_points")]:
        lyr = QgsVectorLayer(p, nm, "ogr")
        if lyr.isValid():
            project.addMapLayer(lyr)

print("\n" + "=" * 70)
print("STEP 2c COMPLETE.")
print("=" * 70)
print("Recentered cut lines :", CUT_OUT)
print("Thalweg centerline   :", CL_OUT)
print("Thalweg points       :", THAL_OUT, "(coords + shift, for inundation)")
print("Raw profiles  (CSV)  :", CSV_OUT)
print("Model profiles (dat) :", DAT_OUT)
print("Model profiles (CSV) :", MODEL_CSV, "(WITH x,y -> inundation mapping)")
print("\nVERIFY:")
print("  - shift_off_m in thalweg_points shows how far each XS moved")
print("  - replot from xs_profiles_model.gp.dat: thalweg should sit near offset 0,")
print("    and flagged XS (%s) should be a smooth blend of their neighbors" % INTERPOLATE_XS)
print("\nNEXT: bank stations + Manning's n, then assemble the RAS .g01 geometry.")
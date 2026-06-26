# =============================================================================
# ras_xs_profiles.py   (QGIS Python Console)
#
# HEC-RAS PIPELINE -- STEP 2b: sample the station-elevation PROFILE of each
# cross-section cut line from the 1 m DEM. This turns the plan-view cut lines
# (step 2a) into the actual cross-section shapes RAS uses: for each XS, ground
# elevation vs. offset across the section.
#
# INPUT  (from earlier steps, in <SITE>/outputs_RAS/)
#   cut_lines.gpkg     one line per XS (xs_id, station_m, river_sta) -- step 2a
#   ras_terrain.tif    1 m DEM clipped to the corridor bbox          -- step 1
#
# OUTPUT (in <SITE>/outputs_RAS/)
#   xs_profiles.csv    long-format, one row per sample point:
#                      xs_id, station_m, river_sta_ft, offset_m, x, y, elev_m
#                      offset_m is signed: left bank negative, 0 at centerline,
#                      right bank positive (matches cut_lines_plan.csv).
#
# Sampling: every SAMPLE_STEP_M along each cut line (1 m matches the DEM). Points
# off the DEM or on nodata are dropped (and counted) so RAS never sees a hole.
#
# Run from: QGIS -> Plugins -> Python Console.
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   exec(open(SCRIPT_DIR + "/ras_xs_profiles.py").read())
# =============================================================================
import os
import csv
import math
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsPointXY,
    QgsCoordinateReferenceSystem
)

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
CUTLINES_NAME = "cut_lines.gpkg"
TERRAIN_NAME  = "ras_terrain.tif"
SAMPLE_STEP_M = 1.0          # sample interval along each cut line (1 m = DEM res)
NODATA_DROP   = True         # drop samples that miss the DEM / hit nodata
CSV_NAME      = "xs_profiles.csv"        # long format (pandas/Excel friendly)
DAT_NAME      = "xs_profiles.gp.dat"     # gnuplot block format (index per XS)
# ---------------------------------------------------------------------------

project   = QgsProject.instance()
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs_RAS")

CUT_PATH = os.path.join(OUT_DIR, CUTLINES_NAME)
DEM_PATH = os.path.join(OUT_DIR, TERRAIN_NAME)
CSV_PATH = os.path.join(OUT_DIR, CSV_NAME)
DAT_PATH = os.path.join(OUT_DIR, DAT_NAME)

print("=" * 70)
print("HEC-RAS STEP 2b -- cross-section station-elevation profiles")
print("  sample step :", SAMPLE_STEP_M, "m")
print("=" * 70)

for p in (CUT_PATH, DEM_PATH):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

cuts = QgsVectorLayer(CUT_PATH, "cut_lines", "ogr")
if not cuts.isValid():
    raise Exception("cut lines invalid: " + CUT_PATH)
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

# --- iterate cut lines, sample along each ----------------------------------
# Collect one profile per XS: a list of (offset, x, y, z|None). We then emit
# both the long CSV and the gnuplot block file from the same data.
profiles = []   # (xs_id, station_m, river_sta_ft, [ (offset,x,y,z), ... ])
n_drop = 0
short_xs = []
for f in sorted(cuts.getFeatures(), key=lambda ff: ff["xs_id"]):
    xs_id   = int(f["xs_id"])
    sta_m   = float(f["station_m"])
    riv_ft  = float(f["river_sta"])
    g = f.geometry()
    line = g.asMultiPolyline()[0] if g.isMultipart() else g.asPolyline()
    if len(line) < 2:
        continue
    pa, pb = line[0], line[-1]              # left end, right end
    seglen = math.hypot(pb.x()-pa.x(), pb.y()-pa.y())
    if seglen < 1e-6:
        continue
    ux, uy = (pb.x()-pa.x())/seglen, (pb.y()-pa.y())/seglen
    half = seglen / 2.0                     # offset 0 at the cut-line center
    samples = []
    kept = 0
    d = 0.0
    while d <= seglen + 1e-9:
        x = pa.x() + ux * d
        y = pa.y() + uy * d
        z = sample(x, y)
        offset = d - half                   # left negative, right positive
        if z is None:
            n_drop += 1
        else:
            kept += 1
        samples.append((offset, x, y, z))
        d += SAMPLE_STEP_M
    profiles.append((xs_id, sta_m, riv_ft, samples))
    if kept < 3:
        short_xs.append(xs_id)

# --- write LONG CSV (pandas/Excel) -----------------------------------------
with open(CSV_PATH, "w", newline="") as fh:
    wr = csv.writer(fh)
    wr.writerow(["xs_id", "station_m", "river_sta_ft",
                 "offset_m", "x", "y", "elev_m"])
    for (xs_id, sta_m, riv_ft, samples) in profiles:
        for (offset, x, y, z) in samples:
            if z is None and NODATA_DROP:
                continue
            wr.writerow([xs_id, "%.3f" % sta_m, "%.3f" % riv_ft,
                         "%.3f" % offset, "%.3f" % x, "%.3f" % y,
                         "" if z is None else "%.3f" % z])

# --- write GNUPLOT BLOCK file (one index per XS) ---------------------------
# Each XS is a block: a comment header carrying metadata, then "offset elev"
# rows, then TWO blank lines so gnuplot sees it as the next `index`.
with open(DAT_PATH, "w", newline="") as fh:
    fh.write("# HEC-RAS cross-section profiles for gnuplot (index = xs_id)\n")
    fh.write("# columns: offset_m  elev_m\n")
    first = True
    for (xs_id, sta_m, riv_ft, samples) in profiles:
        if not first:
            fh.write("\n\n")        # two blank lines BETWEEN blocks only
        first = False
        fh.write("# xs_id=%d  station_m=%.3f  river_sta_ft=%.1f\n"
                 % (xs_id, sta_m, riv_ft))
        for (offset, x, y, z) in samples:
            if z is None:
                continue            # gnuplot: just omit gaps
            fh.write("%.3f %.3f\n" % (offset, z))

print("cross sections sampled : %d" % len(profiles))
print("dropped (nodata/off-DEM): %d" % n_drop)
if short_xs:
    print("    !! XS with <3 valid samples (mostly off-DEM):", short_xs)
    print("       widen the terrain (step 1) or check the corridor at those stations.")
print("wrote", CSV_PATH)
print("wrote", DAT_PATH)

print("\n" + "=" * 70)
print("STEP 2b COMPLETE.")
print("=" * 70)
print("Long CSV    :", CSV_PATH, "(analysis)")
print("gnuplot dat :", DAT_PATH, "(plotting)")
print("\nNEXT: render one PNG per XS:")
print("  gnuplot -e \"dat='%s'; outdir='%s'\" plot_xs.gp"
      % (DAT_PATH, os.path.join(OUT_DIR, "xs_plots")))
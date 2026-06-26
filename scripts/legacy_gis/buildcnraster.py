# =============================================================================
# build_cn_raster.py   (QGIS Python Console)
#
# Step 2 of the curve-number workflow: combine the coregistered land-cover and
# HSG rasters into a per-cell curve-number raster, using the external TR-55
# lookup table (cn_lookup.csv).
#
# Inputs (from prep_cn_grid.py, in <SITE>/outputs/clipped/):
#   landcover_aligned.tif   NLCD class codes on the DEM grid
#   hsg_aligned.tif         HSG codes 1-4 (A-D) on the same grid
#
# Lookup table (cn_lookup.csv): one row per NLCD class, columns
#   cn_<condition>_<HSG> for condition in {poor,fair,good} and HSG in {A,B,C,D}.
#   HYDRO_CONDITION selects which condition's four columns are used. Only the
#   rangeland / woody / open-space rows change with condition; the rest are
#   constant across conditions by design (no hydrologic-condition axis in TR-55).
#
# Output (in <SITE>/outputs/clipped/):
#   cn.tif    Float? no -- Byte CN raster (0-100) on the DEM grid, nodata 255.
#
# A cell is nodata in the CN output if EITHER input is nodata, OR the
# (class, HSG) pair is not in the table. Any unmatched pair is reported so it
# can be added to the CSV -- nothing is silently guessed.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import csv
from osgeo import gdal
from qgis.core import QgsProject, QgsRasterLayer
gdal.UseExceptions()

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"


CN_CSV   = os.path.join(ROOT, "cn_lookup.csv")
HYDRO_CONDITION = "poor"          # "poor" | "fair" | "good"

LC_NAME  = "landcover_aligned.tif"
HSG_NAME = "hsg_aligned.tif"
OUT_NAME = "cn.tif"

NODATA   = 255                    # CN output nodata
HSG_LETTER = {1: "A", 2: "B", 3: "C", 4: "D"}
ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

cond = HYDRO_CONDITION.strip().lower()
if cond not in ("poor", "fair", "good"):
    raise Exception("HYDRO_CONDITION must be poor/fair/good, got: %r" % HYDRO_CONDITION)

site_path = os.path.join(ROOT, SITE_DIR)
CLIP_DIR  = os.path.join(site_path, "outputs", "clipped")
lc_path   = os.path.join(CLIP_DIR, LC_NAME)
hsg_path  = os.path.join(CLIP_DIR, HSG_NAME)
out_path  = os.path.join(CLIP_DIR, OUT_NAME)

print("Site     :", site_path)
print("Condition:", cond)
print("Lookup   :", CN_CSV)

for p in (lc_path, hsg_path, CN_CSV):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

# --- load lookup: (nlcd_class, hsg_letter) -> CN for the chosen condition ----
lut = {}
with open(CN_CSV, newline="") as f:
    rdr = csv.DictReader(f)
    for row in rdr:
        try:
            cls = int(row["nlcd_class"])
        except (KeyError, ValueError):
            continue
        for letter in ("A", "B", "C", "D"):
            col = "cn_%s_%s" % (cond, letter)
            val = row.get(col, "").strip()
            if val != "":
                lut[(cls, letter)] = int(round(float(val)))
print("Lookup pairs loaded:", len(lut))

# --- open inputs ------------------------------------------------------------
lc_ds  = gdal.Open(lc_path)
hsg_ds = gdal.Open(hsg_path)
nx, ny = lc_ds.RasterXSize, lc_ds.RasterYSize
if (hsg_ds.RasterXSize, hsg_ds.RasterYSize) != (nx, ny):
    raise Exception("land cover and HSG grids differ -- run prep_cn_grid.py first")

lc_b, hsg_b = lc_ds.GetRasterBand(1), hsg_ds.GetRasterBand(1)
lc_nd  = lc_b.GetNoDataValue()
hsg_nd = hsg_b.GetNoDataValue()

# --- create output ----------------------------------------------------------
drv = gdal.GetDriverByName("GTiff")
if os.path.exists(out_path):
    os.remove(out_path)
out_ds = drv.Create(out_path, nx, ny, 1, gdal.GDT_Byte, options=["COMPRESS=LZW"])
out_ds.SetGeoTransform(lc_ds.GetGeoTransform())
out_ds.SetProjection(lc_ds.GetProjection())
out_b = out_ds.GetRasterBand(1)
out_b.SetNoDataValue(NODATA)

# --- process row by row (no numpy dependency) -------------------------------
import struct
combo_counts = {}      # (cls,letter) -> cell count, for the audit summary
unmatched = {}         # (cls,hsg_code) -> count, pairs missing from the table
nd_cells = 0

for r in range(ny):
    lc_row  = struct.unpack("%dB" % nx, lc_b.ReadRaster(0, r, nx, 1))
    hsg_row = struct.unpack("%dB" % nx, hsg_b.ReadRaster(0, r, nx, 1))
    out_row = bytearray(nx)
    for c in range(nx):
        lv, hv = lc_row[c], hsg_row[c]
        if (lc_nd is not None and lv == lc_nd) or (hsg_nd is not None and hv == hsg_nd) \
           or lv == 0 or hv == 0:
            out_row[c] = NODATA; nd_cells += 1; continue
        letter = HSG_LETTER.get(hv)
        cn = lut.get((lv, letter)) if letter else None
        if cn is None:
            out_row[c] = NODATA
            unmatched[(lv, hv)] = unmatched.get((lv, hv), 0) + 1
            continue
        out_row[c] = cn
        combo_counts[(lv, letter)] = combo_counts.get((lv, letter), 0) + 1
    out_b.WriteRaster(0, r, nx, 1, bytes(out_row))

out_b.FlushCache()
lc_ds = hsg_ds = out_ds = None

# --- audit summary ----------------------------------------------------------
print("\nCN raster written:", OUT_NAME, "(%d x %d)" % (nx, ny))
print("Condition applied :", cond)
print("\n(class, HSG) -> CN   cells   [what actually occurred in this site]")
for (cls, letter), n in sorted(combo_counts.items()):
    print("  NLCD %3d  HSG %s  ->  CN %3d   %6d" %
          (cls, letter, lut[(cls, letter)], n))
print("  nodata cells: %d" % nd_cells)

if unmatched:
    print("\n*** UNMATCHED (class,HSG) pairs -- set to nodata, ADD TO cn_lookup.csv:")
    for (lv, hv), n in sorted(unmatched.items()):
        print("    NLCD %s  HSG code %s (%s)  x%d" %
              (lv, hv, HSG_LETTER.get(hv, "?"), n))

# --- load into project ------------------------------------------------------
if ADD_TO_PROJECT:
    rl = QgsRasterLayer(out_path, "cn_%s" % cond)
    if rl.isValid():
        QgsProject.instance().addMapLayer(rl)
        print("\n  added to project: cn_%s" % cond)

print("\nDone.")

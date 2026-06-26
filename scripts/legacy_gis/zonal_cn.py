# =============================================================================
# zonal_cn.py   (QGIS Python Console)
#
# Step 3 of the curve-number workflow: compute the area-weighted mean curve
# number for each subwatershed and write it to a NEW parameters GeoPackage.
#
# Why a new file: subwatersheds.gpkg is the delineation pipeline's output. Re-
# running build_subwatersheds.py would overwrite it and destroy any attributes
# added here. So the hydrologic parameters live in their own file,
# subwatershed_params.gpkg, which this script creates (copying the subwatershed
# geometry + id + area_km2) and which later scripts extend with slope, flow
# length, Tc, etc. The delineation output is never modified.
#
# Method: zonal mean of cn.tif over each subwatershed polygon. Because the CN
# raster is on the DEM's UTM grid (equal-area cells), the unweighted cell mean
# IS the area-weighted CN. Cells that are nodata (255: water-excluded, off-grid,
# or unmatched class/HSG) are ignored by the zonal statistic, so they do not
# drag the average -- the CN is the mean over the classified area only.
#
# Inputs (in <SITE>/outputs/):
#   subwatersheds.gpkg              delineation output (layer 'subwatersheds')
#   clipped/cn.tif                  CN raster from build_cn_raster.py
#
# Output (in <SITE>/outputs/):
#   subwatershed_params.gpkg        polygons with id, area_km2, CN  (+ future cols)
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import processing
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsVectorFileWriter,
    QgsCoordinateTransformContext,
)

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

SUBWS_NAME  = "subwatersheds.gpkg"          # delineation output (read-only here)
SUBWS_LAYER = "subwatersheds"
CN_REL      = "clipped/cn.tif"               # CN raster (within outputs/)
OUT_NAME    = "subwatershed_params.gpkg"     # the growing parameters file
OUT_LAYER   = "subwatershed_params"

CN_FIELD    = "CN"                           # field to write (1 decimal)
CN_DECIMALS = 1
ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
subws     = os.path.join(OUT_DIR, SUBWS_NAME)
cn_tif    = os.path.join(OUT_DIR, CN_REL)
out_path  = os.path.join(OUT_DIR, OUT_NAME)

print("Site  :", site_path)
print("Zones :", subws, "(layer %s)" % SUBWS_LAYER)
print("CN    :", cn_tif)
print("Out   :", out_path)

for p in (subws, cn_tif):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

# --- load subwatersheds ----------------------------------------------------
zones = QgsVectorLayer(subws + "|layername=" + SUBWS_LAYER, "zones_src", "ogr")
if not zones.isValid():
    zones = QgsVectorLayer(subws, "zones_src", "ogr")
if not zones.isValid():
    raise Exception("could not open subwatersheds: " + subws)
print("Subwatersheds:", zones.featureCount())

# --- zonal mean of CN over each polygon ------------------------------------
# native:zonalstatisticsfb returns a NEW layer (does not edit the source),
# with the statistic in a prefixed column. We request mean only.
PREFIX = "cn_"
result = processing.run("native:zonalstatisticsfb", {
    "INPUT": zones,
    "INPUT_RASTER": cn_tif,
    "RASTER_BAND": 1,
    "COLUMN_PREFIX": PREFIX,
    "STATISTICS": [2],          # 2 = mean
    "OUTPUT": "memory:zonal",
})
zonal = result["OUTPUT"]
mean_field = PREFIX + "mean"    # native:zonalstatisticsfb names it <prefix>mean

# --- build the output: copy id/area_km2, add CN (rounded) ------------------
from qgis.core import QgsField, QgsFields, QgsFeature
from qgis.PyQt.QtCore import QVariant

fields = QgsFields()
fields.append(QgsField("id", QVariant.Int))
fields.append(QgsField("area_km2", QVariant.Double))
fields.append(QgsField(CN_FIELD, QVariant.Double))

if os.path.exists(out_path):
    os.remove(out_path)
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName  = OUT_LAYER
writer = QgsVectorFileWriter.create(
    out_path, fields, zones.wkbType(), zones.crs(),
    QgsCoordinateTransformContext(), opts)

n = 0
missing = []
for ft in zonal.getFeatures():
    mean = ft[mean_field]
    fid  = ft["id"] if "id" in zonal.fields().names() else n + 1
    area = ft["area_km2"] if "area_km2" in zonal.fields().names() else None
    of = QgsFeature(fields)
    of.setGeometry(ft.geometry())
    of["id"] = int(fid) if fid is not None else n + 1
    if area is not None:
        of["area_km2"] = float(area)
    if mean is None:
        of[CN_FIELD] = None
        missing.append(of["id"])
    else:
        of[CN_FIELD] = round(float(mean), CN_DECIMALS)
    writer.addFeature(of)
    n += 1
del writer

print("\nWrote %d subwatershed(s) -> %s" % (n, OUT_NAME))
print("\n  id    area_km2     CN")
out_lyr = QgsVectorLayer(out_path + "|layername=" + OUT_LAYER, OUT_LAYER, "ogr")
for ft in sorted(out_lyr.getFeatures(), key=lambda f: (f["id"] is None, f["id"])):
    a = ft["area_km2"]; cn = ft[CN_FIELD]
    print("  %-4s  %9s  %5s" % (
        ft["id"],
        ("%.4f" % a) if a is not None else "-",
        ("%.1f" % cn) if cn is not None else "NULL"))

if missing:
    print("\n*** subwatershed(s) with NULL CN (no classified cells inside):", missing)
    print("    check that cn.tif covers these polygons.")

if ADD_TO_PROJECT and out_lyr.isValid():
    QgsProject.instance().addMapLayer(out_lyr)
    print("\n  added to project:", OUT_LAYER)

print("\nDone.")

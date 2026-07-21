# =============================================================================
# extract_slope.py   (QGIS Python Console)
#
# Adds AVERAGE BASIN SLOPE to each subwatershed in subwatershed_params.gpkg,
# and the CENTROID COORDINATES (UTM easting/northing) of each subwatershed.
#
# This is the first script that APPENDS to the parameters file rather than
# recreating it: it reads the existing subwatershed_params.gpkg (which already
# carries id, area_km2, CN from zonal_cn.py), computes the mean slope per
# polygon from the DEM, and writes a new slope_pct column in place. CN and
# everything else are preserved. Re-running only refreshes slope_pct / centroids.
#
# Slope source: gdaldem slope on the clipped UTM DEM, in PERCENT. The zonal mean
# over each subwatershed is the average basin slope (equal-area UTM cells, so the
# cell mean is the area-weighted slope). Percent is the unit the NRCS lag and
# Kirpich/Bransby-Williams equations use (as decimal S = pct/100 downstream).
#
# Centroid source: pointOnSurface() (NOT centroid()) so the point is guaranteed
# to lie INSIDE the polygon even for concave/crescent subwatersheds. Coordinates
# are in the layer CRS (EPSG:26912, UTM 12N) -> metres easting/northing. Useful
# for dropping HMS subbasin nodes and for reference tables.
#
# Inputs (in <SITE>/outputs/):
#   clipped/cliped_utm_wsclip.tif   the clipped UTM DEM
#   subwatershed_params.gpkg        from zonal_cn.py (layer subwatershed_params)
#
# Output: subwatershed_params.gpkg updated in place with columns:
#   slope_pct    average basin slope (%)
#   centroid_x   point-on-surface easting  (m, layer CRS)
#   centroid_y   point-on-surface northing (m, layer CRS)
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import processing
from osgeo import gdal
from qgis.core import QgsField, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant
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
DEM_REL    = "clipped/cliped_utm_wsclip.tif"
PARAMS_NAME = "subwatershed_params.gpkg"
PARAMS_LAYER = "subwatershed_params"

SLOPE_FIELD = "slope_pct"          # average basin slope, percent
CX_FIELD    = "centroid_x"         # point-on-surface easting  (layer CRS, m)
CY_FIELD    = "centroid_y"         # point-on-surface northing (layer CRS, m)
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------


def value_or_none(value):
    """Convert QGIS NULL/QVariant values to Python None for formatting/math."""

    if value is None:
        return None
    is_null = getattr(value, "isNull", None)
    if callable(is_null) and is_null():
        return None
    return value


def format_number(value, pattern, null_text="-"):
    value = value_or_none(value)
    if value is None:
        return null_text
    return pattern % float(value)
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
dem       = os.path.join(OUT_DIR, DEM_REL)
params    = os.path.join(OUT_DIR, PARAMS_NAME)
slope_tif = os.path.join(OUT_DIR, "clipped", "slope_pct.tif")

print("Site   :", site_path)
print("DEM    :", dem)
print("Params :", params)

for p in (dem, params):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

# --- slope raster (percent) from the DEM -----------------------------------
if os.path.exists(slope_tif):
    os.remove(slope_tif)
gdal.DEMProcessing(
    slope_tif, dem, "slope",
    options=gdal.DEMProcessingOptions(slopeFormat="percent",
                                      creationOptions=["COMPRESS=LZW"]))
print("Slope raster:", os.path.basename(slope_tif))

# --- zonal mean slope onto the params layer (in place) ---------------------
# zonalstatisticsfb writes into the INPUT vector layer when given a file path;
# here we run it on a copy and then write the value back, to keep control of
# the column name and avoid the prefixed auto-name.
layer = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, "params", "ogr")
if not layer.isValid():
    raise Exception("could not open params layer: " + params)

PREFIX = "slp_"
res = processing.run("native:zonalstatisticsfb", {
    "INPUT": layer,
    "INPUT_RASTER": slope_tif,
    "RASTER_BAND": 1,
    "COLUMN_PREFIX": PREFIX,
    "STATISTICS": [2],          # mean
    "OUTPUT": "memory:slp",
})
zonal = res["OUTPUT"]
mean_field = PREFIX + "mean"

# map id -> mean slope
slope_by_id = {}
for ft in zonal.getFeatures():
    slope_by_id[ft["id"]] = ft[mean_field]

# --- write slope_pct + centroid coords back into the gpkg in place ---------
layer.startEditing()
existing = [f.name() for f in layer.fields()]
to_add = [(SLOPE_FIELD, QVariant.Double),
          (CX_FIELD,    QVariant.Double),
          (CY_FIELD,    QVariant.Double)]
new = [QgsField(n, t) for (n, t) in to_add if n not in existing]
if new:
    layer.dataProvider().addAttributes(new)
    layer.updateFields()

idx_slope = layer.fields().indexFromName(SLOPE_FIELD)
idx_cx    = layer.fields().indexFromName(CX_FIELD)
idx_cy    = layer.fields().indexFromName(CY_FIELD)

for ft in layer.getFeatures():
    # slope
    val = slope_by_id.get(ft["id"])
    layer.changeAttributeValue(ft.id(), idx_slope,
                               round(float(val), 3) if val is not None else None)
    # centroid (point-on-surface, guaranteed inside the polygon)
    geom = ft.geometry()
    pos = geom.pointOnSurface()        # QgsGeometry (point)
    if pos and not pos.isEmpty():
        p = pos.asPoint()
        layer.changeAttributeValue(ft.id(), idx_cx, round(p.x(), 3))
        layer.changeAttributeValue(ft.id(), idx_cy, round(p.y(), 3))
    else:
        layer.changeAttributeValue(ft.id(), idx_cx, None)
        layer.changeAttributeValue(ft.id(), idx_cy, None)
layer.commitChanges()

# --- report ----------------------------------------------------------------
print("\nUpdated %s with %s, %s, %s:" % (PARAMS_NAME, SLOPE_FIELD, CX_FIELD, CY_FIELD))
print("\n  id    area_km2     CN   slope_pct      centroid_x     centroid_y")
layer2 = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, PARAMS_LAYER, "ogr")
for ft in sorted(layer2.getFeatures(), key=lambda f: (f["id"] is None, f["id"])):
    cn = ft["CN"] if "CN" in layer2.fields().names() else None
    sp = ft[SLOPE_FIELD]
    cx = ft[CX_FIELD]
    cy = ft[CY_FIELD]
    print("  %-4s  %9s  %5s   %7s   %12s  %12s" % (
        ft["id"],
        format_number(ft["area_km2"], "%.4f"),
        format_number(cn, "%.1f"),
        format_number(sp, "%.3f", "NULL"),
        format_number(cx, "%.2f", "NULL"),
        format_number(cy, "%.2f", "NULL")))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(layer2)
    print("\n  reloaded:", PARAMS_LAYER)

print("\nDone.")

# =============================================================================
# clip_dem_to_watershed.py   (QGIS Python Console)
#
# PHASE 1, step 3: clip the real-elevation DEM (demlr/cliped_utm.tif) to the
# whole-watershed boundary, producing outputs/clipped/cliped_utm_wsclip.tif --
# the real DEM that extract_reaches.py samples for reach endpoint elevations and
# slope. This is a FOCUSED clip of just the DEM; the full multi-layer
# clip-to-watershed (cliptowatershed.py, phase 2) runs later against the final
# subwatershed boundary and will regenerate this file consistently.
#
# Real elevations only: dem_carved.tif is the artificial routing staircase and
# must never be used for slope -- so phase-1 reach slopes come from THIS file.
#
# INPUT
#   <SITE>/demlr/cliped_utm.tif              real-elevation DEM (UTM)
#   <SITE>/outputs/watershed_boundary.gpkg   whole-watershed boundary (phase-1)
#
# OUTPUT
#   <SITE>/outputs/clipped/cliped_utm_wsclip.tif   (CRS stamped explicitly)
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import processing
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsCoordinateReferenceSystem
)

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
DEM_REL       = "demlr/cliped_utm.tif"
BOUNDARY_NAME = "watershed_boundary.gpkg"
OUT_NAME      = "cliped_utm_wsclip.tif"     # exactly what extract_reaches expects
NODATA        = -9999
FALLBACK_EPSG = 26912
ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR = globals().get("OUT_DIR", os.path.join(site_path, "outputs"))
OUT_DIR = os.path.abspath(os.path.expanduser(OUT_DIR))
CLIP_DIR = os.path.join(OUT_DIR, "clipped")
os.makedirs(CLIP_DIR, exist_ok=True)

DEM_PATH = globals().get("DEM_PATH", os.path.join(site_path, DEM_REL))
DEM_PATH = os.path.abspath(os.path.expanduser(DEM_PATH))
BOUNDARY_PATH = globals().get(
    "BOUNDARY_PATH",
    os.path.join(OUT_DIR, BOUNDARY_NAME),
)
BOUNDARY_PATH = os.path.abspath(os.path.expanduser(BOUNDARY_PATH))
OUT_PATH = globals().get("CLIPPED_DEM_PATH", os.path.join(CLIP_DIR, OUT_NAME))
OUT_PATH = os.path.abspath(os.path.expanduser(OUT_PATH))

print("Site     :", site_path)
print("DEM      :", DEM_PATH)
print("Boundary :", BOUNDARY_PATH)
print("Output   :", OUT_PATH)

for p in (DEM_PATH, BOUNDARY_PATH):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

dem = QgsRasterLayer(DEM_PATH, "dem")
if not dem.isValid():
    raise Exception("DEM invalid: " + DEM_PATH)
dem_crs = dem.crs() if dem.crs().isValid() else QgsCoordinateReferenceSystem("EPSG:%d" % FALLBACK_EPSG)
crs_authid = dem_crs.authid() or ("EPSG:%d" % FALLBACK_EPSG)
print("DEM CRS  :", crs_authid)

# release any loaded layer pointing at the output (Windows lock-safe), delete file
proj = QgsProject.instance()
norm_out = os.path.normpath(OUT_PATH).lower()
for lyr in list(proj.mapLayers().values()):
    try:
        if os.path.normpath(lyr.source().split("|")[0]).lower() == norm_out:
            proj.removeMapLayer(lyr.id())
    except Exception:
        pass
if os.path.exists(OUT_PATH):
    os.remove(OUT_PATH)

processing.run("gdal:cliprasterbymasklayer", {
    "INPUT": DEM_PATH,
    "MASK": BOUNDARY_PATH,
    "SOURCE_CRS": dem_crs,
    "TARGET_CRS": dem_crs,
    "NODATA": NODATA,
    "ALPHA_BAND": False,
    "CROP_TO_CUTLINE": True,
    "KEEP_RESOLUTION": True,
    "OPTIONS": "",
    "DATA_TYPE": 0,
    "OUTPUT": OUT_PATH,
})

if not os.path.exists(OUT_PATH):
    raise Exception("clip produced no output (CRS mismatch between DEM and boundary?)")

chk = QgsRasterLayer(OUT_PATH, "cliped_utm_wsclip")
print("Clipped DEM written:", OUT_PATH)
print("  size:", chk.width(), "x", chk.height(), "| CRS:", chk.crs().authid() or "NONE")
if ADD_TO_PROJECT and chk.isValid():
    proj.addMapLayer(chk)

print("\nDone. extract_reaches.py can now sample real elevations from")
print("clipped/cliped_utm_wsclip.tif.")

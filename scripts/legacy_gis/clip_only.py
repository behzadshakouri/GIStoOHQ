# =============================================================================
# Clip vector layers to a DEM's extent, handling CRS mismatches correctly.
# NO raster merge. Reads the DEM by file path only to get its extent and CRS,
# reprojects each vector INTO the DEM's CRS, then clips.
#
# All generated files go to <SITE>/outputs/ . Intermediates go to
# <SITE>/outputs/temp/ so they are easy to delete later.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import processing
from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

# --- settings (set the two paths ONCE) -------------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

DEM_REL  = "demlr/cliped_utm.tif"        # DEM, relative to the site folder
CLIP_SUFFIX = "_clip"
USE_VISIBLE_ONLY = True                  # only clip ticked vector layers
# ---------------------------------------------------------------------------

# --- derived paths ---------------------------------------------------------
site_path = os.path.join(ROOT, SITE_DIR)
DEM_PATH  = os.path.join(site_path, DEM_REL)
OUT_DIR   = os.path.join(site_path, "outputs")
TEMP_DIR  = os.path.join(OUT_DIR, "temp")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

project = QgsProject.instance()
root = project.layerTreeRoot()

dem = QgsRasterLayer(DEM_PATH, "ref_dem")
if not dem.isValid():
    raise Exception("DEM invalid / not found: " + DEM_PATH)
dem_crs = dem.crs()
dem_ext = dem.extent()
print("Root      :", ROOT)
print("Site      :", site_path)
print("DEM       :", DEM_PATH)
print("Outputs   :", OUT_DIR)
print("  DEM CRS :", dem_crs.authid())
print("  DEM size:", dem.width(), "x", dem.height())

def is_visible(layer):
    node = root.findLayer(layer.id())
    return node is not None and node.isVisible()

vectors = []
for lyr in project.mapLayers().values():
    if isinstance(lyr, QgsVectorLayer):
        if USE_VISIBLE_ONLY and not is_visible(lyr):
            continue
        vectors.append(lyr)

print(f"\nClipping {len(vectors)} vector layer(s) to the DEM extent "
      f"(reprojecting each into {dem_crs.authid()})...")
for v in vectors:
    print(f"\n- {v.name()}   (source CRS {v.crs().authid()})")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in v.name())
    out_path = os.path.join(OUT_DIR, f"{safe}{CLIP_SUFFIX}.gpkg")
    try:
        src = v.source()
        if v.crs().authid() != dem_crs.authid():
            print(f"    reprojecting {v.crs().authid()} -> {dem_crs.authid()}")
            rep = processing.run("native:reprojectlayer", {
                "INPUT": src, "TARGET_CRS": dem_crs, "OUTPUT": "TEMPORARY_OUTPUT"})
            src = rep["OUTPUT"]
        else:
            print("    CRS already matches DEM")
        extent_str = (f"{dem_ext.xMinimum()},{dem_ext.xMaximum()},"
                      f"{dem_ext.yMinimum()},{dem_ext.yMaximum()} [{dem_crs.authid()}]")
        clip = processing.run("native:extractbyextent", {
            "INPUT": src, "EXTENT": extent_str, "CLIP": True, "OUTPUT": out_path})
        cl = QgsVectorLayer(clip["OUTPUT"], f"{v.name()}{CLIP_SUFFIX}", "ogr")
        if cl.isValid():
            project.addMapLayer(cl)
            print(f"    -> {out_path}  ({cl.featureCount()} features)")
        else:
            print(f"    WARNING: output invalid for {v.name()}")
    except Exception as ex:
        print(f"    ERROR clipping {v.name()}: {ex}")

print("\nDone. Clipped vectors in:", OUT_DIR)
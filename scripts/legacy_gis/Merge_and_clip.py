# =============================================================================
# DIAGNOSTIC merge + clip script
# Run from: QGIS -> Plugins -> Python Console -> paste/exec
#
# This version PRINTS exactly which files it sees before doing anything, so we
# can find why a geographic (EPSG:4269) full-extent raster is being merged when
# you expect only the small UTM clip.
#
# It also lets you bypass layer-detection entirely and name the raster file
# directly (RASTER_OVERRIDE), which removes any ambiguity about which file is
# being used.
# =============================================================================

import os
import math
import processing
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform
)

# --- settings --------------------------------------------------------------
USE_VISIBLE_ONLY = True
OUT_DIR          = None                 # None -> project folder
MERGED_NAME      = "merged_dem.tif"
REPROJ_NAME      = "merged_dem_utm.tif"
CLIP_SUFFIX      = "_clip"
NODATA_VALUE     = -9999
TARGET_EPSG      = None                  # None = auto from data center
RESAMPLING       = 1

# If set to a file path, the script uses THIS raster and ignores the layer list
# entirely. This is the sure way to merge exactly what you intend.
RASTER_OVERRIDE  = None
# e.g. RASTER_OVERRIDE = "/home/arash/Dropbox/Chloeta/NHA/WS3_GIS/AZ12-100/demlr/cliped_utm.tif"

DRY_RUN          = False                  # True = ONLY print diagnostics, do nothing
# ---------------------------------------------------------------------------

project = QgsProject.instance()
root = project.layerTreeRoot()

if OUT_DIR is None:
    OUT_DIR = project.homePath() or os.path.expanduser("~")

def is_visible(layer):
    node = root.findLayer(layer.id())
    return node is not None and node.isVisible()

print("=" * 70)
print("DIAGNOSTIC: every layer in the project")
print("=" * 70)
print(f"{'name':<28} {'type':<7} {'visible':<8} {'CRS':<12} source")
print("-" * 70)

all_rasters, all_vectors = [], []
for lyr in project.mapLayers().values():
    if isinstance(lyr, QgsRasterLayer):
        kind = "RASTER"
    elif isinstance(lyr, QgsVectorLayer):
        kind = "VECTOR"
    else:
        kind = "OTHER"
    vis = is_visible(lyr)
    print(f"{lyr.name()[:27]:<28} {kind:<7} {str(vis):<8} {lyr.crs().authid():<12} {lyr.source()}")
    if kind == "RASTER":
        all_rasters.append((lyr, vis))
    elif kind == "VECTOR":
        all_vectors.append((lyr, vis))

print("-" * 70)
print(f"Total rasters in project: {len(all_rasters)} | vectors: {len(all_vectors)}")

# Which rasters WOULD be merged under current settings?
if RASTER_OVERRIDE:
    print(f"\nRASTER_OVERRIDE set -> using ONLY this file:\n   {RASTER_OVERRIDE}")
    if not os.path.exists(RASTER_OVERRIDE):
        print("   !!! WARNING: that file does not exist on disk.")
    merge_sources = [RASTER_OVERRIDE]
else:
    selected = [lyr for (lyr, vis) in all_rasters if (vis or not USE_VISIBLE_ONLY)]
    print(f"\nRasters that WOULD be merged ({len(selected)}):")
    for lyr in selected:
        print(f"   name='{lyr.name()}'  CRS={lyr.crs().authid()}")
        print(f"      source = {lyr.source()}")
        # also report extent + size so a full-extent geographic raster is obvious
        e = lyr.extent()
        print(f"      extent = {e.xMinimum():.4f},{e.yMinimum():.4f} .. "
              f"{e.xMaximum():.4f},{e.yMaximum():.4f}")
        print(f"      size   = {lyr.width()} x {lyr.height()} px")
    merge_sources = [lyr.source() for lyr in selected]

print("\n" + "=" * 70)
print("Merge input file list (exactly what gdal:merge will receive):")
for s in merge_sources:
    print("   ", s)
print("=" * 70)

if DRY_RUN:
    print("\nDRY_RUN = True -> stopping here. No files written.")
    print("Read the lines above:")
    print("  * If the merge input is NOT your cliped_utm.tif, that's the bug.")
    print("  * Look for a raster whose CRS is EPSG:4269 or whose size is huge.")
    print("  * To force the correct file: set RASTER_OVERRIDE to its path,")
    print("    set DRY_RUN = False, and re-run.")
else:
    if not merge_sources:
        raise Exception("No raster sources selected. Tick a raster or set RASTER_OVERRIDE.")

    os.makedirs(OUT_DIR, exist_ok=True)
    print("\nOutput directory:", OUT_DIR)

    # --- merge -------------------------------------------------------------
    merged_path = os.path.join(OUT_DIR, MERGED_NAME)
    print("Merging ->", merged_path)
    merge_res = processing.run("gdal:merge", {
        "INPUT": merge_sources,
        "PCT": False, "SEPARATE": False,
        "NODATA_INPUT": None, "NODATA_OUTPUT": NODATA_VALUE,
        "OPTIONS": "", "EXTRA": "", "DATA_TYPE": 5,
        "OUTPUT": merged_path,
    })
    merged_path = merge_res["OUTPUT"]
    merged_layer = QgsRasterLayer(merged_path, "merged_dem")
    if not merged_layer.isValid():
        raise Exception("Merged raster failed to load: " + merged_path)
    src_crs = merged_layer.crs()
    print("Merge done. Native CRS:", src_crs.authid(),
          "| size:", merged_layer.width(), "x", merged_layer.height())

    # --- target UTM --------------------------------------------------------
    def utm_epsg_for(lon, lat):
        zone = int(math.floor((lon + 180.0) / 6.0) + 1)
        return (26900 + zone if lat >= 0 else 32700 + zone), zone

    if TARGET_EPSG is not None:
        target_crs = QgsCoordinateReferenceSystem.fromEpsgId(int(TARGET_EPSG))
    else:
        e = merged_layer.extent()
        cx = (e.xMinimum() + e.xMaximum()) / 2.0
        cy = (e.yMinimum() + e.yMaximum()) / 2.0
        geo = QgsCoordinateReferenceSystem.fromEpsgId(4326)
        c = QgsCoordinateTransform(src_crs, geo, project).transform(cx, cy)
        epsg, zone = utm_epsg_for(c.x(), c.y())
        target_crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
        print(f"Auto UTM zone {zone}N -> {target_crs.authid()}")

    reproj_path = os.path.join(OUT_DIR, REPROJ_NAME)
    if src_crs.authid() == target_crs.authid():
        print("Already in target UTM; no reprojection.")
        reproj_path = merged_path
        reproj_layer = merged_layer
    else:
        print(f"Reprojecting {src_crs.authid()} -> {target_crs.authid()} ...")
        warp_res = processing.run("gdal:warpreproject", {
            "INPUT": merged_path, "SOURCE_CRS": src_crs, "TARGET_CRS": target_crs,
            "RESAMPLING": RESAMPLING, "NODATA": NODATA_VALUE,
            "TARGET_RESOLUTION": None, "OPTIONS": "", "DATA_TYPE": 0,
            "TARGET_EXTENT": None, "TARGET_EXTENT_CRS": None,
            "MULTITHREADING": False, "EXTRA": "", "OUTPUT": reproj_path,
        })
        reproj_path = warp_res["OUTPUT"]
        reproj_layer = QgsRasterLayer(reproj_path, "merged_dem_utm")
    project.addMapLayer(reproj_layer)
    print(">>> Terrain file:", reproj_path,
          "| size:", reproj_layer.width(), "x", reproj_layer.height())

    # --- clip vectors to terrain extent ------------------------------------
    e = reproj_layer.extent()
    extent_str = (f"{e.xMinimum()},{e.xMaximum()},{e.yMinimum()},{e.yMaximum()} "
                  f"[{reproj_layer.crs().authid()}]")
    print("Clip extent:", extent_str)

    vecs = [lyr for (lyr, vis) in all_vectors if (vis or not USE_VISIBLE_ONLY)]
    for v in vecs:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in v.name())
        out_path = os.path.join(OUT_DIR, f"{safe}{CLIP_SUFFIX}.gpkg")
        try:
            clip_res = processing.run("gdal:clipvectorbyextent", {
                "INPUT": v.source(), "EXTENT": extent_str,
                "OPTIONS": "", "OUTPUT": "TEMPORARY_OUTPUT"})
            rep = processing.run("native:reprojectlayer", {
                "INPUT": clip_res["OUTPUT"], "TARGET_CRS": target_crs,
                "OUTPUT": out_path})
            cl = QgsVectorLayer(rep["OUTPUT"], f"{v.name()}{CLIP_SUFFIX}", "ogr")
            if cl.isValid():
                project.addMapLayer(cl)
                print(f"  clipped: {v.name()} -> {out_path}")
        except Exception as ex:
            print(f"  ERROR clipping {v.name()}: {ex}")

    print("\nDone.")
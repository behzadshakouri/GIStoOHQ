# =============================================================================
# load_cn_inputs.py   (QGIS Python Console)
#
# Load the curve-number INPUT layers into the project so the next step
# (cliptowatershed.py) sweeps them into the clip-to-watershed. After clipping,
# prepcngrid.py expects these exact clipped filenames in outputs/clipped/:
#       cliped_utm_wsclip.tif        (DEM, already handled elsewhere)
#       nlcd_*_wsclip.tif            (land cover)
#       hsg_wsclip.tif               (HSG raster)
# Because cliptowatershed.py names each clipped file from the LAYER NAME plus
# the _wsclip suffix, this script loads each input under the precise name that
# yields those downstream filenames:
#       nlcd_2023_<SITE>   ->  nlcd_2023_<SITE>_wsclip.tif   (matches nlcd_*_wsclip.tif)
#       hsg                ->  hsg_wsclip.tif                (matches HSG_NAME)
#
# Source files (per site, <SITE> = last component of SITE_DIR, e.g. AZ12-100):
#   <SITE>/landcover/nlcd_<NLCD_YEAR>_<SITE>.tif
#   <SITE>/soils/hsg.tif
#   <SITE>/soils/hydrologic_soil_groups.gpkg   (vector; provenance/inspection)
#
# The .gpkg is NOT consumed by prepcngrid.py (it uses the hsg.tif raster); it is
# loaded for inspection and rides along through the clip harmlessly. Missing
# optional files WARN rather than stop, so a site without the vector still runs.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

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
NLCD_YEAR = "2023"             # year token in the NLCD filename
LANDCOVER_SUBDIR = "landcover"
SOILS_SUBDIR     = "soils"

NLCD_LAYER_NAME = None         # None -> derive "nlcd_<YEAR>_<SITE>" (do not change;
                               # the downstream glob depends on this name)
HSG_RASTER_LAYER_NAME = "hsg"  # -> hsg_wsclip.tif after clipping (prepcngrid HSG_NAME)
HSG_VECTOR_LAYER_NAME = "hydrologic_soil_groups"

REPLACE_IF_LOADED = True       # drop an already-loaded layer of the same source first
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
SITE_ID   = os.path.basename(os.path.normpath(SITE_DIR))   # e.g. "AZ12-100"

nlcd_name = NLCD_LAYER_NAME or ("nlcd_%s_%s" % (NLCD_YEAR, SITE_ID))

nlcd_path = os.path.join(site_path, LANDCOVER_SUBDIR, "nlcd_%s_%s.tif" % (NLCD_YEAR, SITE_ID))
hsg_ras_path = os.path.join(site_path, SOILS_SUBDIR, "hsg.tif")
hsg_vec_path = os.path.join(site_path, SOILS_SUBDIR, "hydrologic_soil_groups.gpkg")

print("Site    :", site_path)
print("Site id :", SITE_ID)
print("NLCD    :", nlcd_path, "-> layer '%s'" % nlcd_name)
print("HSG tif :", hsg_ras_path, "-> layer '%s'" % HSG_RASTER_LAYER_NAME)
print("HSG gpkg:", hsg_vec_path, "-> layer '%s'" % HSG_VECTOR_LAYER_NAME)

project = QgsProject.instance()

def _drop_existing(source_path):
    """Remove any already-loaded layer pointing at this file (avoids duplicates)."""
    if not REPLACE_IF_LOADED:
        return
    norm = os.path.normpath(source_path).lower()
    for lyr in list(project.mapLayers().values()):
        try:
            if os.path.normpath(lyr.source().split("|")[0]).lower() == norm:
                project.removeMapLayer(lyr.id())
        except Exception:
            pass

def load_raster(path, name, required):
    if not os.path.isfile(path):
        msg = "  %s NOT FOUND: %s" % ("MISSING (required)" if required else "skipped (optional)", path)
        print(msg)
        if required:
            raise Exception("required input not found: " + path)
        return None
    _drop_existing(path)
    rl = QgsRasterLayer(path, name)
    if not rl.isValid():
        if required:
            raise Exception("raster failed to load: " + path)
        print("  WARNING: raster invalid, skipped:", path)
        return None
    project.addMapLayer(rl)
    print("  loaded raster:", name)
    return rl

def load_vector(path, name, required):
    if not os.path.isfile(path):
        print("  %s NOT FOUND: %s" % ("MISSING (required)" if required else "skipped (optional)", path))
        if required:
            raise Exception("required input not found: " + path)
        return None
    _drop_existing(path)
    vl = QgsVectorLayer(path, name, "ogr")
    if not vl.isValid():
        if required:
            raise Exception("vector failed to load: " + path)
        print("  WARNING: vector invalid, skipped:", path)
        return None
    project.addMapLayer(vl)
    print("  loaded vector:", name, "(%d features)" % vl.featureCount())
    return vl

print("\nLoading CN-input layers into the project...")
load_raster(nlcd_path, nlcd_name, required=True)
load_raster(hsg_ras_path, HSG_RASTER_LAYER_NAME, required=True)
load_vector(hsg_vec_path, HSG_VECTOR_LAYER_NAME, required=False)

print("\nDone. These layers are now in the project and will be clipped to the")
print("watershed by cliptowatershed.py. After clipping, prepcngrid.py will find:")
print("  nlcd_%s_%s_wsclip.tif  and  hsg_wsclip.tif" % (NLCD_YEAR, SITE_ID))

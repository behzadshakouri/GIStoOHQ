# =============================================================================
# sligo_creek_runners.py
#
# Copy/paste runner templates for the retained QGIS Python Console scripts.
# Set RUNNER to one of:
#   "merge_and_clip"
#   "phase1"
#   "phase2"
#
# Then run this file from the QGIS Python Console, or copy the block you need.
# =============================================================================

import os


# =============================================================================
# SHARED PROJECT PATHS
# =============================================================================

ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
SITE_DIR = ""
SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"

OUT_DIR = (
    "/mnt/3rd900/Projects/SligoCreek_QGIS/"
    "outputs"
)

DEM_PATH = (
    "/mnt/3rd900/Projects/SligoCreek_QGIS/"
    "clipped_dem_utm.tif"
)

OUTLET_PATH = (
    "/mnt/3rd900/Projects/SligoCreek_QGIS/"
    "outputs/outlet.shp"
)

FLOWLINE_PATH = (
    "/mnt/3rd900/Projects/SligoCreek_QGIS/"
    "hydro/NHD_H_0206_HU4_Shape/Shape/NHDFlowline.shp"
)

FORCE = True
TARGET_EPSG = 26918
DRY_RUN = False


# =============================================================================
# SELECT THE RUNNER TO EXECUTE
# =============================================================================

RUNNER = "phase1"


# =============================================================================
# MERGE_AND_CLIP RUNNER
# =============================================================================

if RUNNER == "merge_and_clip":
    # Merge source DEM tiles, reproject to TARGET_EPSG, and write the Phase 1
    # real-elevation DEM. Override these values if you want QA outputs instead
    # of the default Phase 1 DEM path.
    OUT_DIR = os.path.join(ROOT, SITE_DIR, "demlr")
    CLIPPED_NAME = "cliped_utm.tif"

    CLIP_MODE = "RASTER_EXTENT"
    CLIP_RASTER = (
        "/mnt/3rd900/Projects/SligoCreek_QGIS/"
        "landcover/nlcd_2023_SligoCreek_Mouth.tif"
    )

    DEM_SEARCH_MODE = "ROOT_ONLY"
    ADD_OUTPUTS_TO_PROJECT = True

    exec(open(os.path.join(SCRIPT_DIR, "Merge_and_clip.py")).read())


# =============================================================================
# PHASE 1 RUNNER
# =============================================================================

elif RUNNER == "phase1":
    # Phase 1 consumes the real-elevation DEM, outlet, clipped flowlines, and
    # precomputed flow_dir/flow_acc rasters under OUT_DIR.
    exec(open(os.path.join(SCRIPT_DIR, "run_phase1.py")).read())


# =============================================================================
# PHASE 2 RUNNER
# =============================================================================

elif RUNNER == "phase2":
    # Phase 2 starts after Phase 1 outputs have been inspected and
    # outputs/pour_points.shp has been created by the operator.
    POUR_POINTS_PATH = os.path.join(OUT_DIR, "pour_points.shp")
    WATERSHED_PATH = os.path.join(OUT_DIR, "watershed_boundary.gpkg")
    REACHES_PATH = os.path.join(OUT_DIR, "reaches.gpkg")
    JUNCTIONS_PATH = os.path.join(OUT_DIR, "junctions.gpkg")

    exec(open(os.path.join(SCRIPT_DIR, "run_phase2.py")).read())


else:
    raise Exception(
        "RUNNER must be one of: merge_and_clip, phase1, phase2"
    )

# =============================================================================
# Merge_and_clip.py
#
# QGIS Python Console script for:
#   1. Diagnosing loaded raster/vector layers.
#   2. Selecting source DEM tiles safely.
#   3. Merging the DEM tiles.
#   4. Reprojecting the merged DEM to UTM.
#   5. Clipping the projected DEM by:
#        - NLCD raster extent (default and safest), or
#        - an explicitly supplied polygon vector mask.
#
# The script supports runner variables defined BEFORE exec(...).
#
# Minimal runner:
#
# ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
# SITE_DIR = ""
# SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
# exec(open(f"{SCRIPT_DIR}/Merge_and_clip.py").read())
#
# Runner with options:
#
# ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
# SITE_DIR = ""
# SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
#
# CLIP_MODE = "RASTER_EXTENT"
# CLIP_RASTER = (
#     "/mnt/3rd900/Projects/SligoCreek_QGIS/landcover/"
#     "nlcd_2023_SligoCreek_Mouth.tif"
# )
#
# DEM_SEARCH_MODE = "ROOT_ONLY"
# TARGET_EPSG = 26918
# FORCE = True
#
# exec(open(f"{SCRIPT_DIR}/Merge_and_clip.py").read())
#
# Vector-mask runner:
#
# ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
# SITE_DIR = ""
# SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
#
# CLIP_MODE = "VECTOR"
# CLIP_VECTOR = (
#     "/mnt/3rd900/Projects/SligoCreek_QGIS/Vectorized_clip.gpkg"
# )
#
# exec(open(f"{SCRIPT_DIR}/Merge_and_clip.py").read())
#
# Main outputs:
#   merged_dem.tif
#   merged_dem_utm.tif
#   clipped_dem_utm.tif
# =============================================================================

import os
import math
import gc
import processing

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsWkbTypes,
)


# =============================================================================
# RUNNER-OVERRIDABLE SETTINGS
# =============================================================================

# Project paths.
ROOT = globals().get(
    "ROOT",
    "/mnt/3rd900/Projects/SligoCreek_QGIS",
)
SITE_DIR = globals().get("SITE_DIR", "")

# Output directory:
#   None -> ROOT/SITE_DIR
OUT_DIR = globals().get("OUT_DIR", None)

# Source DEM selection.
#
# ROOT_ONLY:
#   Search only directly inside ROOT/SITE_DIR. This is the safe default and
#   prevents old DEM downloads under subdirectories such as demlr from being
#   merged.
#
# LOADED_ONLY:
#   Use only loaded QGIS raster layers matching DEM_PREFIXES.
#
# EXPLICIT:
#   Use DEM_FILES supplied by the runner.
#
# RECURSIVE:
#   Search all subdirectories. Use only when intentionally needed.
DEM_SEARCH_MODE = str(
    globals().get("DEM_SEARCH_MODE", "ROOT_ONLY")
).upper()

DEM_PREFIXES = tuple(
    globals().get("DEM_PREFIXES", ("USGS_13_",))
)

# Explicit DEM file list. Used when DEM_SEARCH_MODE="EXPLICIT".
DEM_FILES = list(globals().get("DEM_FILES", []))

# Skip merge and use one existing raster directly.
RASTER_OVERRIDE = globals().get("RASTER_OVERRIDE", None)

# Clip mode:
#
# RASTER_EXTENT:
#   Clip to CLIP_RASTER's rectangular extent. This is the default.
#
# VECTOR:
#   Clip by polygon geometry using CLIP_VECTOR.
#
# AUTO:
#   Use CLIP_VECTOR when explicitly supplied and valid; otherwise use
#   CLIP_RASTER extent.
CLIP_MODE = str(
    globals().get("CLIP_MODE", "RASTER_EXTENT")
).upper()

# Raster defining the clipping extent.
CLIP_RASTER = globals().get(
    "CLIP_RASTER",
    os.path.join(
        ROOT,
        SITE_DIR,
        "landcover",
        "nlcd_2023_SligoCreek_Mouth.tif",
    ),
)

# Polygon vector mask. This must be explicitly supplied for VECTOR mode.
# It may be:
#   /path/file.shp
#   /path/file.gpkg
#   /path/file.gpkg|layername=mask
#   a QgsVectorLayer object
CLIP_VECTOR = globals().get("CLIP_VECTOR", None)

# Repair and dissolve vectorized land-cover polygons before clipping.
FIX_VECTOR_GEOMETRIES = bool(
    globals().get("FIX_VECTOR_GEOMETRIES", True)
)
DISSOLVE_VECTOR_MASK = bool(
    globals().get("DISSOLVE_VECTOR_MASK", True)
)

# Target CRS:
#   None -> automatically select UTM from DEM center.
TARGET_EPSG = globals().get("TARGET_EPSG", None)

# Output names.
MERGED_NAME = globals().get("MERGED_NAME", "merged_dem.tif")
REPROJECTED_NAME = globals().get(
    "REPROJECTED_NAME",
    "merged_dem_utm.tif",
)
CLIPPED_NAME = globals().get(
    "CLIPPED_NAME",
    "clipped_dem_utm.tif",
)

# Processing behavior.
NODATA_VALUE = float(globals().get("NODATA_VALUE", -9999.0))
RESAMPLING = int(globals().get("RESAMPLING", 1))
FORCE = bool(globals().get("FORCE", True))
DRY_RUN = bool(globals().get("DRY_RUN", False))
ADD_OUTPUTS_TO_PROJECT = bool(
    globals().get("ADD_OUTPUTS_TO_PROJECT", True)
)

GTIFF_OPTIONS = globals().get(
    "GTIFF_OPTIONS",
    "COMPRESS=DEFLATE|PREDICTOR=2|ZLEVEL=6|"
    "TILED=YES|BIGTIFF=IF_SAFER",
)


# =============================================================================
# DERIVED PATHS
# =============================================================================

PROJECT = QgsProject.instance()
LAYER_TREE_ROOT = PROJECT.layerTreeRoot()

SITE_PATH = os.path.abspath(os.path.join(ROOT, SITE_DIR))

if OUT_DIR is None:
    OUT_DIR = SITE_PATH
else:
    OUT_DIR = os.path.abspath(OUT_DIR)

os.makedirs(OUT_DIR, exist_ok=True)

MERGED_PATH = os.path.join(OUT_DIR, MERGED_NAME)
REPROJECTED_PATH = os.path.join(OUT_DIR, REPROJECTED_NAME)
CLIPPED_PATH = os.path.join(OUT_DIR, CLIPPED_NAME)

CLIP_RASTER = (
    os.path.abspath(CLIP_RASTER)
    if CLIP_RASTER
    else None
)

EXCLUDED_OUTPUT_NAMES = {
    MERGED_NAME.lower(),
    REPROJECTED_NAME.lower(),
    CLIPPED_NAME.lower(),
    "merged_dem.tif",
    "merged_dem_utm.tif",
    "clipped_dem.tif",
    "clipped_dem_utm.tif",
    "cliped_dem.tif",
    "cliped_dem_utm.tif",
    "cliped_utm.tif",
}


# =============================================================================
# HELPERS
# =============================================================================

def separator(character="=", width=78):
    print(character * width)


def source_path(source):
    """Remove QGIS provider suffixes such as |layername=..."""
    if not source:
        return ""
    return source.split("|", 1)[0]


def normalized_source(layer):
    try:
        path = source_path(layer.source())
        return os.path.abspath(path) if path else ""
    except Exception:
        return ""


def layer_is_visible(layer):
    node = LAYER_TREE_ROOT.findLayer(layer.id())
    return node is not None and node.isVisible()


def load_raster(path, name):
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        raise Exception(
            "Raster failed to load:\n"
            f"  {path}"
        )
    return layer


def load_vector(value, name):
    if isinstance(value, QgsVectorLayer):
        layer = value
    else:
        layer = QgsVectorLayer(value, name, "ogr")

    if not layer.isValid():
        raise Exception(
            "Vector layer failed to load:\n"
            f"  {value}"
        )

    if (
        QgsWkbTypes.geometryType(layer.wkbType())
        != QgsWkbTypes.PolygonGeometry
    ):
        raise Exception(
            "Clip vector must be a polygon layer:\n"
            f"  {layer.name()}"
        )

    return layer


def raster_summary(layer):
    extent = layer.extent()
    return (
        f"   name='{layer.name()}'\n"
        f"      CRS    = {layer.crs().authid() or 'NONE'}\n"
        f"      source = {layer.source()}\n"
        f"      extent = "
        f"{extent.xMinimum():.4f},{extent.yMinimum():.4f} .. "
        f"{extent.xMaximum():.4f},{extent.yMaximum():.4f}\n"
        f"      size   = {layer.width()} x {layer.height()} px"
    )


def vector_summary(layer):
    extent = layer.extent()
    return (
        f"   name='{layer.name()}'\n"
        f"      CRS      = {layer.crs().authid() or 'NONE'}\n"
        f"      geometry = "
        f"{QgsWkbTypes.displayString(layer.wkbType())}\n"
        f"      source   = {layer.source()}\n"
        f"      features = {layer.featureCount()}\n"
        f"      extent   = "
        f"{extent.xMinimum():.4f},{extent.yMinimum():.4f} .. "
        f"{extent.xMaximum():.4f},{extent.yMaximum():.4f}"
    )


def remove_loaded_layers_for_file(path):
    target = os.path.normcase(
        os.path.abspath(source_path(path))
    )

    for layer in list(PROJECT.mapLayers().values()):
        existing = normalized_source(layer)
        if existing and os.path.normcase(existing) == target:
            print(
                "Removing loaded output before overwrite:",
                layer.name(),
            )
            PROJECT.removeMapLayer(layer.id())

    gc.collect()


def prepare_output(path):
    if not os.path.exists(path):
        return

    if not FORCE:
        raise Exception(
            "Output already exists and FORCE=False:\n"
            f"  {path}"
        )

    remove_loaded_layers_for_file(path)

    try:
        os.remove(path)
    except Exception as exc:
        raise Exception(
            "Could not remove existing output:\n"
            f"  {path}\n"
            f"Reason: {exc}"
        )


def add_raster_to_project(path, name):
    if not ADD_OUTPUTS_TO_PROJECT:
        return

    target = os.path.normcase(os.path.abspath(path))

    for layer in PROJECT.mapLayers().values():
        existing = normalized_source(layer)
        if existing and os.path.normcase(existing) == target:
            return

    layer = QgsRasterLayer(path, name)
    if layer.isValid():
        PROJECT.addMapLayer(layer)
        print("Added raster to project:", name)


def is_source_dem(path):
    if not path:
        return False

    path = os.path.abspath(source_path(path))

    if not os.path.isfile(path):
        return False

    filename = os.path.basename(path)
    lower = filename.lower()

    if not lower.endswith((".tif", ".tiff")):
        return False

    if lower in EXCLUDED_OUTPUT_NAMES:
        return False

    if CLIP_RASTER:
        if os.path.normcase(path) == os.path.normcase(CLIP_RASTER):
            return False

    return filename.startswith(DEM_PREFIXES)


def processing_extent(rectangle, crs):
    return (
        f"{rectangle.xMinimum()},"
        f"{rectangle.xMaximum()},"
        f"{rectangle.yMinimum()},"
        f"{rectangle.yMaximum()} "
        f"[{crs.authid()}]"
    )


def transform_extent(rectangle, source_crs, destination_crs):
    if source_crs == destination_crs:
        return QgsRectangle(rectangle)

    transform = QgsCoordinateTransform(
        source_crs,
        destination_crs,
        PROJECT,
    )
    return transform.transformBoundingBox(rectangle)


def extents_overlap(rectangle_a, rectangle_b):
    return not (
        rectangle_a.xMaximum() <= rectangle_b.xMinimum()
        or rectangle_a.xMinimum() >= rectangle_b.xMaximum()
        or rectangle_a.yMaximum() <= rectangle_b.yMinimum()
        or rectangle_a.yMinimum() >= rectangle_b.yMaximum()
    )


def automatic_target_crs(raster):
    source_crs = raster.crs()

    if not source_crs.isValid():
        raise Exception(
            "Cannot determine UTM zone because DEM CRS is invalid."
        )

    center = raster.extent().center()
    geographic = QgsCoordinateReferenceSystem.fromEpsgId(4326)

    transform = QgsCoordinateTransform(
        source_crs,
        geographic,
        PROJECT,
    )

    point = transform.transform(center)
    longitude = point.x()
    latitude = point.y()

    zone = int(math.floor((longitude + 180.0) / 6.0) + 1)
    zone = max(1, min(zone, 60))

    if latitude >= 0:
        if zone <= 23:
            epsg = 26900 + zone
            datum = "NAD83"
        else:
            epsg = 32600 + zone
            datum = "WGS 84"
        hemisphere = "N"
    else:
        epsg = 32700 + zone
        datum = "WGS 84"
        hemisphere = "S"

    crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)

    return (
        crs,
        epsg,
        zone,
        hemisphere,
        datum,
        longitude,
        latitude,
    )


# =============================================================================
# PROJECT DIAGNOSTIC
# =============================================================================

loaded_rasters = []
loaded_vectors = []

separator()
print("DIAGNOSTIC: every layer in the QGIS project")
separator()
print(
    f"{'name':<30} "
    f"{'type':<8} "
    f"{'visible':<9} "
    f"{'CRS':<13} "
    "source"
)
print("-" * 130)

for layer in PROJECT.mapLayers().values():
    visible = layer_is_visible(layer)

    if isinstance(layer, QgsRasterLayer):
        layer_type = "RASTER"
        loaded_rasters.append((layer, visible))
    elif isinstance(layer, QgsVectorLayer):
        layer_type = "VECTOR"
        loaded_vectors.append((layer, visible))
    else:
        layer_type = "OTHER"

    print(
        f"{layer.name()[:29]:<30} "
        f"{layer_type:<8} "
        f"{str(visible):<9} "
        f"{(layer.crs().authid() or ''):<13} "
        f"{layer.source()}"
    )

print("-" * 130)
print(
    f"Total rasters in project: {len(loaded_rasters)} | "
    f"vectors: {len(loaded_vectors)}"
)


# =============================================================================
# SELECT SOURCE DEM FILES
# =============================================================================

if RASTER_OVERRIDE:
    override_path = os.path.abspath(
        source_path(RASTER_OVERRIDE)
    )

    if not os.path.isfile(override_path):
        raise Exception(
            "RASTER_OVERRIDE does not exist:\n"
            f"  {override_path}"
        )

    merge_sources = [override_path]
    skip_merge = True

else:
    merge_sources = []

    if DEM_SEARCH_MODE == "EXPLICIT":
        for item in DEM_FILES:
            path = os.path.abspath(source_path(item))

            if not os.path.isfile(path):
                raise Exception(
                    "DEM file does not exist:\n"
                    f"  {path}"
                )

            if path not in merge_sources:
                merge_sources.append(path)

    elif DEM_SEARCH_MODE == "LOADED_ONLY":
        for layer, _visible in loaded_rasters:
            path = normalized_source(layer)

            if is_source_dem(path) and path not in merge_sources:
                merge_sources.append(path)

    elif DEM_SEARCH_MODE == "ROOT_ONLY":
        for filename in os.listdir(SITE_PATH):
            path = os.path.abspath(
                os.path.join(SITE_PATH, filename)
            )

            if is_source_dem(path) and path not in merge_sources:
                merge_sources.append(path)

    elif DEM_SEARCH_MODE == "RECURSIVE":
        for directory, _subdirs, filenames in os.walk(SITE_PATH):
            for filename in filenames:
                path = os.path.abspath(
                    os.path.join(directory, filename)
                )

                if is_source_dem(path) and path not in merge_sources:
                    merge_sources.append(path)

    else:
        raise Exception(
            "DEM_SEARCH_MODE must be one of:\n"
            "  ROOT_ONLY\n"
            "  LOADED_ONLY\n"
            "  EXPLICIT\n"
            "  RECURSIVE"
        )

    merge_sources.sort()
    skip_merge = len(merge_sources) == 1


if not merge_sources:
    raise Exception(
        "No source DEM files were selected.\n\n"
        f"DEM_SEARCH_MODE = {DEM_SEARCH_MODE}\n"
        f"DEM_PREFIXES = {DEM_PREFIXES}\n\n"
        "Use DEM_FILES with DEM_SEARCH_MODE='EXPLICIT' when needed."
    )


print()
print(f"Source DEM raster(s) selected ({len(merge_sources)}):")

for path in merge_sources:
    layer = load_raster(path, os.path.basename(path))
    print(raster_summary(layer))

separator()
print("Merge input file list:")
for path in merge_sources:
    print("   ", path)
separator()


# =============================================================================
# SELECT CLIP METHOD
# =============================================================================

if CLIP_MODE not in ("RASTER_EXTENT", "VECTOR", "AUTO"):
    raise Exception(
        "CLIP_MODE must be RASTER_EXTENT, VECTOR, or AUTO."
    )

if CLIP_MODE == "AUTO":
    selected_clip_mode = "VECTOR" if CLIP_VECTOR else "RASTER_EXTENT"
else:
    selected_clip_mode = CLIP_MODE


clip_raster_layer = None
clip_vector_layer = None

if selected_clip_mode == "RASTER_EXTENT":
    if not CLIP_RASTER:
        raise Exception(
            "CLIP_RASTER is required for RASTER_EXTENT mode."
        )

    if not os.path.isfile(CLIP_RASTER):
        raise Exception(
            "CLIP_RASTER does not exist:\n"
            f"  {CLIP_RASTER}"
        )

    clip_raster_layer = load_raster(
        CLIP_RASTER,
        "clip_raster",
    )

elif selected_clip_mode == "VECTOR":
    if not CLIP_VECTOR:
        raise Exception(
            "CLIP_VECTOR must be explicitly supplied for VECTOR mode.\n\n"
            "Example:\n"
            'CLIP_VECTOR = "/path/to/mask.gpkg"'
        )

    clip_vector_layer = load_vector(
        CLIP_VECTOR,
        "clip_vector",
    )


separator()
print("RUN CONFIGURATION")
separator()
print("ROOT             :", ROOT)
print("SITE_DIR         :", SITE_DIR)
print("OUT_DIR          :", OUT_DIR)
print("DEM_SEARCH_MODE  :", DEM_SEARCH_MODE)
print("CLIP_MODE        :", CLIP_MODE)
print("Selected clipping:", selected_clip_mode)
print("CLIP_RASTER      :", CLIP_RASTER)
print(
    "CLIP_VECTOR      :",
    CLIP_VECTOR.source()
    if isinstance(CLIP_VECTOR, QgsVectorLayer)
    else CLIP_VECTOR,
)
print("TARGET_EPSG      :", TARGET_EPSG)
print("FORCE            :", FORCE)
print("DRY_RUN          :", DRY_RUN)

if clip_raster_layer:
    print()
    print("Clip raster:")
    print(raster_summary(clip_raster_layer))

if clip_vector_layer:
    print()
    print("Clip vector:")
    print(vector_summary(clip_vector_layer))

print()
print("Merged output    :", MERGED_PATH)
print("Projected output :", REPROJECTED_PATH)
print("Clipped output   :", CLIPPED_PATH)

if DRY_RUN:
    print()
    print("DRY_RUN=True: stopping before file creation.")
    raise SystemExit


# =============================================================================
# MERGE DEM FILES
# =============================================================================

if RASTER_OVERRIDE:
    native_dem_path = merge_sources[0]
    print()
    print("RASTER_OVERRIDE set; merge skipped.")

elif skip_merge:
    native_dem_path = merge_sources[0]
    print()
    print("Only one DEM selected; merge skipped.")

else:
    prepare_output(MERGED_PATH)

    print()
    print("Merging DEM tiles ->", MERGED_PATH)

    result = processing.run("gdal:merge", {
        "INPUT": merge_sources,
        "PCT": False,
        "SEPARATE": False,
        "NODATA_INPUT": None,
        "NODATA_OUTPUT": NODATA_VALUE,
        "OPTIONS": GTIFF_OPTIONS,
        "EXTRA": "",
        "DATA_TYPE": 5,
        "OUTPUT": MERGED_PATH,
    })

    native_dem_path = result["OUTPUT"]


native_dem = load_raster(native_dem_path, "merged_dem")

print(
    "Native DEM:",
    native_dem_path,
    "| CRS:",
    native_dem.crs().authid(),
    "| size:",
    native_dem.width(),
    "x",
    native_dem.height(),
)


# =============================================================================
# DETERMINE TARGET CRS
# =============================================================================

if TARGET_EPSG is None:
    (
        target_crs,
        target_epsg,
        utm_zone,
        hemisphere,
        datum,
        center_lon,
        center_lat,
    ) = automatic_target_crs(native_dem)

    print(
        f"DEM center: longitude={center_lon:.6f}, "
        f"latitude={center_lat:.6f}"
    )
    print(
        f"Auto UTM zone {utm_zone}{hemisphere} -> "
        f"{datum} EPSG:{target_epsg}"
    )
else:
    target_epsg = int(TARGET_EPSG)
    target_crs = QgsCoordinateReferenceSystem.fromEpsgId(
        target_epsg
    )

    if not target_crs.isValid():
        raise Exception(
            f"Invalid TARGET_EPSG: {TARGET_EPSG}"
        )

    print("Configured target CRS:", target_crs.authid())


# =============================================================================
# REPROJECT DEM
# =============================================================================

if native_dem.crs() == target_crs:
    projected_dem_path = native_dem_path
    projected_dem = native_dem

    print()
    print(
        "DEM already uses target CRS; reprojection skipped:",
        target_crs.authid(),
    )

else:
    prepare_output(REPROJECTED_PATH)

    print()
    print(
        "Reprojecting",
        native_dem.crs().authid(),
        "->",
        target_crs.authid(),
    )

    result = processing.run("gdal:warpreproject", {
        "INPUT": native_dem_path,
        "SOURCE_CRS": native_dem.crs(),
        "TARGET_CRS": target_crs,
        "RESAMPLING": RESAMPLING,
        "NODATA": NODATA_VALUE,
        "TARGET_RESOLUTION": None,
        "OPTIONS": GTIFF_OPTIONS,
        "DATA_TYPE": 5,
        "TARGET_EXTENT": None,
        "TARGET_EXTENT_CRS": None,
        "MULTITHREADING": True,
        "EXTRA": "",
        "OUTPUT": REPROJECTED_PATH,
    })

    projected_dem_path = result["OUTPUT"]
    projected_dem = load_raster(
        projected_dem_path,
        "merged_dem_utm",
    )


print(
    "Terrain file:",
    projected_dem_path,
    "| CRS:",
    projected_dem.crs().authid(),
    "| size:",
    projected_dem.width(),
    "x",
    projected_dem.height(),
)


# =============================================================================
# PREPARE AND VALIDATE CLIP FOOTPRINT
# =============================================================================

projected_extent = projected_dem.extent()
projected_crs = projected_dem.crs()

prepare_output(CLIPPED_PATH)

if selected_clip_mode == "RASTER_EXTENT":
    clip_extent_projected = transform_extent(
        clip_raster_layer.extent(),
        clip_raster_layer.crs(),
        projected_crs,
    )

    if not extents_overlap(
        projected_extent,
        clip_extent_projected,
    ):
        raise Exception(
            "The clip raster extent does not overlap the projected DEM.\n\n"
            f"DEM CRS: {projected_crs.authid()}\n"
            f"Clip raster CRS: {clip_raster_layer.crs().authid()}\n"
            f"DEM extent: {processing_extent(projected_extent, projected_crs)}\n"
            f"Clip extent in DEM CRS: "
            f"{processing_extent(clip_extent_projected, projected_crs)}"
        )

    clip_extent_text = processing_extent(
        clip_extent_projected,
        projected_crs,
    )

    print()
    separator()
    print("CLIPPING BY RASTER EXTENT")
    separator()
    print("Template raster :", CLIP_RASTER)
    print("Template CRS    :", clip_raster_layer.crs().authid())
    print("DEM CRS         :", projected_crs.authid())
    print("Clip extent     :", clip_extent_text)
    print("Output          :", CLIPPED_PATH)

    result = processing.run("gdal:cliprasterbyextent", {
        "INPUT": projected_dem_path,
        "PROJWIN": clip_extent_text,
        "OVERCRS": False,
        "NODATA": NODATA_VALUE,
        "OPTIONS": GTIFF_OPTIONS,
        "DATA_TYPE": 5,
        "EXTRA": "",
        "OUTPUT": CLIPPED_PATH,
    })

else:
    vector_extent_projected = transform_extent(
        clip_vector_layer.extent(),
        clip_vector_layer.crs(),
        projected_crs,
    )

    if not extents_overlap(
        projected_extent,
        vector_extent_projected,
    ):
        raise Exception(
            "The vector mask does not overlap the projected DEM.\n\n"
            f"DEM CRS: {projected_crs.authid()}\n"
            f"Vector CRS: {clip_vector_layer.crs().authid()}\n"
            f"DEM extent: {processing_extent(projected_extent, projected_crs)}\n"
            f"Vector extent in DEM CRS: "
            f"{processing_extent(vector_extent_projected, projected_crs)}"
        )

    working_mask = clip_vector_layer

    print()
    separator()
    print("PREPARING VECTOR MASK")
    separator()
    print(vector_summary(working_mask))

    if FIX_VECTOR_GEOMETRIES:
        print("Fixing vector geometries...")

        fixed = processing.run("native:fixgeometries", {
            "INPUT": working_mask,
            "METHOD": 1,
            "OUTPUT": "TEMPORARY_OUTPUT",
        })

        working_mask = fixed["OUTPUT"]

    if DISSOLVE_VECTOR_MASK:
        print("Dissolving all polygons into one mask...")

        dissolved = processing.run("native:dissolve", {
            "INPUT": working_mask,
            "FIELD": [],
            "SEPARATE_DISJOINT": False,
            "OUTPUT": "TEMPORARY_OUTPUT",
        })

        working_mask = dissolved["OUTPUT"]

    print()
    separator()
    print("CLIPPING BY VECTOR MASK")
    separator()
    print("Output:", CLIPPED_PATH)

    result = processing.run("gdal:cliprasterbymasklayer", {
        "INPUT": projected_dem_path,
        "MASK": working_mask,
        "SOURCE_CRS": projected_crs,
        "TARGET_CRS": projected_crs,
        "TARGET_EXTENT": None,
        "NODATA": NODATA_VALUE,
        "ALPHA_BAND": False,
        "CROP_TO_CUTLINE": True,
        "KEEP_RESOLUTION": True,
        "SET_RESOLUTION": False,
        "X_RESOLUTION": None,
        "Y_RESOLUTION": None,
        "MULTITHREADING": True,
        "OPTIONS": GTIFF_OPTIONS,
        "DATA_TYPE": 5,
        "EXTRA": "",
        "OUTPUT": CLIPPED_PATH,
    })


# =============================================================================
# VERIFY OUTPUT
# =============================================================================

clipped_dem_path = result["OUTPUT"]
clipped_dem = load_raster(
    clipped_dem_path,
    "clipped_dem_utm",
)

clipped_extent = clipped_dem.extent()

separator()
print("FINAL OUTPUT")
separator()
print("Clip mode   :", selected_clip_mode)
print("Output path :", clipped_dem_path)
print("CRS         :", clipped_dem.crs().authid())
print(
    "Size        :",
    clipped_dem.width(),
    "x",
    clipped_dem.height(),
)
print(
    "Extent      :",
    f"{clipped_extent.xMinimum():.4f},"
    f"{clipped_extent.yMinimum():.4f} .. "
    f"{clipped_extent.xMaximum():.4f},"
    f"{clipped_extent.yMaximum():.4f}",
)

if clipped_dem.width() <= 0 or clipped_dem.height() <= 0:
    raise Exception(
        "Clipped DEM has invalid dimensions."
    )

if (
    clipped_dem.width() >= projected_dem.width()
    and clipped_dem.height() >= projected_dem.height()
):
    print(
        "WARNING: clipped raster is not smaller than the full projected DEM."
    )
else:
    print(
        "Clip verification: output dimensions are smaller than the full DEM."
    )

if os.path.isfile(MERGED_PATH):
    add_raster_to_project(MERGED_PATH, "merged_dem")

if os.path.isfile(REPROJECTED_PATH):
    add_raster_to_project(
        REPROJECTED_PATH,
        "merged_dem_utm",
    )

add_raster_to_project(
    CLIPPED_PATH,
    "clipped_dem_utm",
)

print()
print("Done.")

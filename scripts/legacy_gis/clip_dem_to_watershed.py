# =============================================================================
# clip_dem_to_watershed.py   (QGIS Python Console)
#
# PHASE 1, step 3: clip the real-elevation DEM to the whole-watershed boundary,
# producing outputs/clipped/cliped_utm_wsclip.tif -- the real DEM that
# extract_reaches.py samples for reach endpoint elevations and slope. This is a
# FOCUSED clip of just the DEM; the full multi-layer clip-to-watershed
# (cliptowatershed.py, phase 2) runs later against the final subwatershed
# boundary and will regenerate this file consistently.
#
# Real elevations only: dem_carved.tif is the artificial routing staircase and
# must never be used for slope -- so phase-1 reach slopes come from THIS file.
#
# INPUT
#   DEM_PATH, defaulting to <SITE>/demlr/cliped_utm.tif
#   BOUNDARY_PATH, defaulting to <OUT_DIR>/watershed_boundary.gpkg
#
# OUTPUT
#   CLIPPED_DEM_PATH, defaulting to <OUT_DIR>/clipped/cliped_utm_wsclip.tif
#
# The legacy output spelling "cliped_utm_wsclip.tif" is intentionally retained
# because extract_reaches.py expects that exact filename.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import os

import fiona
import processing
import rasterio
from rasterio.mask import mask as rasterio_mask
from rasterio.warp import transform_geom

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
)



def clip_raster_with_rasterio(input_path, mask_path, output_path, nodata, options, layer_name=None):
    """Clip a raster by a vector mask without requiring QGIS' GDAL provider."""

    creation_options = {}
    for item in str(options or "").split("|"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        creation_options[key.lower()] = value

    with rasterio.open(input_path) as src:
        mask_layer = layer_name
        if mask_layer is None:
            try:
                layers = fiona.listlayers(mask_path)
            except Exception:
                layers = []
            if layers:
                mask_layer = layers[0]

        with fiona.open(mask_path, layer=mask_layer) as vector_src:
            geometries = []
            vector_crs = vector_src.crs_wkt or vector_src.crs
            for feature in vector_src:
                geom = feature.get("geometry")
                if not geom:
                    continue
                if vector_crs and src.crs and vector_crs != src.crs:
                    geom = transform_geom(vector_crs, src.crs, geom)
                geometries.append(geom)

        if not geometries:
            raise Exception("Mask contains no geometries: " + mask_path)

        out_image, out_transform = rasterio_mask(
            src,
            geometries,
            crop=True,
            nodata=nodata,
            filled=True,
        )
        out_meta = src.meta.copy()
        out_meta.update(
            {
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "nodata": nodata,
            }
        )
        out_meta.update(creation_options)

        with rasterio.open(output_path, "w", **out_meta) as dst:
            dst.write(out_image)


# =============================================================================
# RUNNER-OVERRIDABLE SETTINGS
# =============================================================================

ROOT = globals().get(
    "ROOT",
    "/mnt/3rd900/Projects/SligoCreek_QGIS",
)
SITE_DIR = globals().get("SITE_DIR", "")

DEM_REL = globals().get("DEM_REL", os.path.join("demlr", "cliped_utm.tif"))
BOUNDARY_NAME = globals().get("BOUNDARY_NAME", "watershed_boundary.gpkg")
OUT_NAME = globals().get("OUT_NAME", "cliped_utm_wsclip.tif")

NODATA = float(globals().get("NODATA", -9999.0))
FALLBACK_EPSG = int(globals().get("FALLBACK_EPSG", 26912))
ADD_TO_PROJECT = bool(globals().get("ADD_TO_PROJECT", True))
FORCE = bool(globals().get("FORCE", True))
GTIFF_OPTIONS = globals().get(
    "GTIFF_OPTIONS",
    "COMPRESS=LZW|TILED=YES|BIGTIFF=IF_SAFER",
)


# =============================================================================
# PATH RESOLUTION
# =============================================================================

ROOT = os.path.abspath(os.path.expanduser(ROOT))
if os.path.isabs(SITE_DIR):
    site_path = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    site_path = os.path.abspath(os.path.join(ROOT, SITE_DIR))

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
print("FORCE    :", FORCE)


# =============================================================================
# INPUT CHECKS
# =============================================================================

for path in (DEM_PATH, BOUNDARY_PATH):
    if not os.path.isfile(path):
        raise Exception("not found: " + path)


dem = QgsRasterLayer(DEM_PATH, "dem")
if not dem.isValid():
    raise Exception("DEM invalid: " + DEM_PATH)

boundary = QgsVectorLayer(
    BOUNDARY_PATH + "|layername=watershed_boundary",
    "watershed_boundary",
    "ogr",
)

if not boundary.isValid():
    boundary = QgsVectorLayer(
        BOUNDARY_PATH,
        "watershed_boundary",
        "ogr",
    )

if not boundary.isValid():
    raise Exception("watershed boundary invalid: " + BOUNDARY_PATH)

if boundary.featureCount() < 1:
    raise Exception(
        "watershed boundary contains no features: " + BOUNDARY_PATH
    )


# =============================================================================
# DETERMINE CRS
# =============================================================================

if dem.crs().isValid():
    dem_crs = dem.crs()
else:
    dem_crs = QgsCoordinateReferenceSystem(
        "EPSG:%d" % FALLBACK_EPSG
    )

crs_authid = dem_crs.authid() or (
    "EPSG:%d" % FALLBACK_EPSG
)

print("DEM CRS  :", crs_authid)
print("Mask CRS :", boundary.crs().authid() or "NONE")
print("Features :", boundary.featureCount())

if (
    boundary.crs().isValid()
    and dem_crs.isValid()
    and boundary.crs() != dem_crs
):
    print(
        "WARNING: DEM and watershed boundary CRS differ. "
        "GDAL will transform the mask during clipping."
    )


# =============================================================================
# RELEASE LOADED OUTPUT AND HANDLE OVERWRITE
# =============================================================================

proj = QgsProject.instance()
norm_out = os.path.normcase(os.path.abspath(OUT_PATH))

for layer in list(proj.mapLayers().values()):
    try:
        layer_path = layer.source().split("|", 1)[0]
        norm_layer_path = os.path.normcase(os.path.abspath(layer_path))

        if norm_layer_path == norm_out:
            proj.removeMapLayer(layer.id())
            print("Removed loaded output layer:", layer.name())

    except Exception:
        pass

if os.path.exists(OUT_PATH):
    if not FORCE:
        print("Output exists and FORCE is False:")
        print(" ", OUT_PATH)
        print("\nSkipping DEM clipping.")

    else:
        os.remove(OUT_PATH)

        for sidecar in (
            OUT_PATH + ".aux.xml",
            OUT_PATH + ".ovr",
        ):
            if os.path.exists(sidecar):
                os.remove(sidecar)

        print("Removed existing output:", OUT_PATH)


# =============================================================================
# CLIP DEM
# =============================================================================

if FORCE or not os.path.exists(OUT_PATH):
    print("\nClipping real-elevation DEM to watershed boundary...")

    try:
        processing.run(
            "gdal:cliprasterbymasklayer",
            {
                "INPUT": DEM_PATH,
                "MASK": BOUNDARY_PATH,
                "SOURCE_CRS": dem_crs,
                "TARGET_CRS": dem_crs,
                "TARGET_EXTENT": None,
                "NODATA": NODATA,
                "ALPHA_BAND": False,
                "CROP_TO_CUTLINE": True,
                "KEEP_RESOLUTION": True,
                "SET_RESOLUTION": False,
                "X_RESOLUTION": None,
                "Y_RESOLUTION": None,
                "MULTITHREADING": True,
                "OPTIONS": GTIFF_OPTIONS,
                "DATA_TYPE": 0,
                "EXTRA": "",
                "OUTPUT": OUT_PATH,
            },
        )
    except Exception as exc:
        print("QGIS GDAL clip failed; using rasterio fallback:", exc)
        clip_raster_with_rasterio(
            DEM_PATH,
            BOUNDARY_PATH,
            OUT_PATH,
            NODATA,
            GTIFF_OPTIONS,
            layer_name="watershed_boundary",
        )


# =============================================================================
# VERIFY OUTPUT
# =============================================================================

if not os.path.exists(OUT_PATH):
    raise Exception(
        "clip produced no output "
        "(possible CRS mismatch between DEM and boundary): "
        + OUT_PATH
    )

chk = QgsRasterLayer(
    OUT_PATH,
    "cliped_utm_wsclip",
)

if not chk.isValid():
    raise Exception(
        "clipped DEM was created but is invalid: " + OUT_PATH
    )

if chk.width() <= 0 or chk.height() <= 0:
    raise Exception(
        "clipped DEM has invalid dimensions: " + OUT_PATH
    )

print("\nClipped DEM written:", OUT_PATH)
print(
    "  size:",
    chk.width(),
    "x",
    chk.height(),
    "| CRS:",
    chk.crs().authid() or "NONE",
)
print("  extent:", chk.extent().toString())

if ADD_TO_PROJECT:
    proj.addMapLayer(chk)
    print("  loaded into QGIS project")

print("\nDone. extract_reaches.py can now sample real elevations from")
print("outputs/clipped/cliped_utm_wsclip.tif.")
print("")
print("Use DEM_PATH or cliped_utm_wsclip.tif for real")
print("elevation and slope calculations, not dem_carved.tif.")

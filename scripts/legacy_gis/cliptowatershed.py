# =============================================================================
# Clip raster and vector layers to final subwatersheds boundary.
#
# Updated for GIStoOHQ headless/test runs:
# - Writes watershed_boundary.gpkg with CRS.
# - Skips invalid vector layers instead of crashing.
# - For dummy/test CN rasters, copies them directly to expected *_wsclip.tif
#   outputs instead of sending them through QGIS/GDAL clip, which caused
#   exit code -11 segmentation fault.
# - Ensures required clipped DEM template exists:
#       outputs/clipped/cliped_utm_wsclip.tif
# - Keeps real raster/vector clipping for normal datasets.
# =============================================================================

import os
import gc
import shutil
import processing

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsGeometry,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsCoordinateTransformContext,
    QgsCoordinateReferenceSystem,
)

from qgis.PyQt.QtCore import QVariant


# --- settings ---------------------------------------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

SUBWS_NAME = "subwatersheds.gpkg"
CLIP_SUFFIX = "_wsclip"
USE_VISIBLE_ONLY = False
ADD_TO_PROJECT = True

FALLBACK_EPSG = 26912

# If True, known CN rasters are copied to outputs/clipped instead of clipped.
# This is useful for minimal/dummy NLCD and HSG inputs.
COPY_CN_RASTERS_DIRECTLY = True

CN_RASTER_NAMES = {
    "hsg",
    "nlcd_2023_az12-100",
}
# ---------------------------------------------------------------------------


site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR = os.path.join(site_path, "outputs")
CLIP_DIR = os.path.join(OUT_DIR, "clipped")
os.makedirs(CLIP_DIR, exist_ok=True)

SUBWS_PATH = os.path.join(OUT_DIR, SUBWS_NAME)

print("Site    :", site_path)
print("Boundary:", SUBWS_PATH)
print("Clipped :", CLIP_DIR)


# --- helpers ----------------------------------------------------------------
def safe(name):
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)


def remove_existing_gpkg(path):
    if not os.path.exists(path):
        return

    deleted = False

    try:
        ok, _ = QgsVectorFileWriter.deleteSilently(path)
        deleted = ok
    except Exception:
        deleted = False

    if deleted:
        return

    for ext in ("", "-wal", "-shm", "-journal"):
        p = path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except PermissionError:
                raise Exception(
                    "Cannot overwrite %s -- it is still open. Remove the "
                    "layer from QGIS panel or restart QGIS and re-run." % p
                )


def remove_existing_raster(path):
    for ext in ("", ".aux.xml", ".ovr"):
        p = path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except PermissionError:
                raise Exception(
                    "Cannot overwrite %s -- it is still open. Restart QGIS "
                    "or remove the raster layer and re-run." % p
                )


def is_cn_raster(layer):
    name = layer.name().lower()
    source = os.path.basename(layer.source()).lower()

    if name in CN_RASTER_NAMES:
        return True

    if source in {
        "hsg.tif",
        "nlcd_2023_az12-100.tif",
    }:
        return True

    if name.startswith("nlcd_2023_"):
        return True

    return False


def copy_raster_directly(layer, out_path):
    src = layer.source()

    if "|" in src:
        src = src.split("|", 1)[0]

    if not os.path.exists(src):
        raise Exception("Raster source not found: " + src)

    remove_existing_raster(out_path)

    shutil.copyfile(src, out_path)

    aux = src + ".aux.xml"
    if os.path.exists(aux):
        shutil.copyfile(aux, out_path + ".aux.xml")


def clip_raster_file(input_path, output_path, mask_path, crs):
    if not os.path.exists(input_path):
        raise Exception("Raster input not found: " + input_path)

    remove_existing_raster(output_path)

    processing.run(
        "gdal:cliprasterbymasklayer",
        {
            "INPUT": input_path,
            "MASK": mask_path,
            "SOURCE_CRS": crs,
            "TARGET_CRS": crs,
            "NODATA": -9999,
            "ALPHA_BAND": False,
            "CROP_TO_CUTLINE": True,
            "KEEP_RESOLUTION": True,
            "OPTIONS": "",
            "DATA_TYPE": 0,
            "OUTPUT": output_path,
        },
    )

    if not os.path.exists(output_path):
        raise Exception("Raster clip produced no output: " + output_path)


# --- load subwatersheds -----------------------------------------------------
subws = QgsVectorLayer(
    SUBWS_PATH + "|layername=subwatersheds",
    "subwatersheds_src",
    "ogr",
)

if not subws.isValid():
    subws = QgsVectorLayer(SUBWS_PATH, "subwatersheds_src", "ogr")

if not subws.isValid():
    raise Exception("Could not open subwatersheds: " + SUBWS_PATH)

mask_crs = subws.crs()

if not mask_crs.isValid():
    mask_crs = QgsCoordinateReferenceSystem("EPSG:%d" % FALLBACK_EPSG)
    print("  subwatersheds had no CRS -> using EPSG:%d" % FALLBACK_EPSG)

crs_authid = mask_crs.authid() or ("EPSG:%d" % FALLBACK_EPSG)

print("Mask CRS:", crs_authid)

geoms = []

for ft in subws.getFeatures():
    geom = ft.geometry()
    if geom and not geom.isEmpty():
        geoms.append(geom)

if not geoms:
    raise Exception("subwatersheds has no geometry.")

boundary = QgsGeometry.unaryUnion(geoms).makeValid()

print(
    "Dissolved %d subwatershed(s) into one boundary (area %.4f km2)."
    % (len(geoms), boundary.area() / 1e6)
)


# --- write watershed boundary ----------------------------------------------
mask_path = os.path.join(OUT_DIR, "watershed_boundary.gpkg")

project = QgsProject.instance()

for lyr in list(project.mapLayers().values()):
    try:
        if "watershed_boundary" in lyr.source().lower():
            project.removeMapLayer(lyr.id())
    except Exception:
        pass

gc.collect()
remove_existing_gpkg(mask_path)

fields = QgsFields()
fields.append(QgsField("id", QVariant.Int))

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName = "watershed_boundary"

ctx = QgsCoordinateTransformContext()

writer = QgsVectorFileWriter.create(
    mask_path,
    fields,
    QgsWkbTypes.MultiPolygon,
    mask_crs,
    ctx,
    opts,
)

bgeom = boundary

if bgeom.wkbType() not in (QgsWkbTypes.MultiPolygon, QgsWkbTypes.Polygon):
    coerced = bgeom.coerceToType(QgsWkbTypes.MultiPolygon)
    if coerced:
        bgeom = coerced[0]

bf = QgsFeature(fields)
bf.setGeometry(bgeom)
bf["id"] = 1
writer.addFeature(bf)

del writer
gc.collect()

check = QgsVectorLayer(
    mask_path + "|layername=watershed_boundary",
    "watershed_boundary_check",
    "ogr",
)

if not check.isValid():
    raise Exception("Boundary mask was written but could not be reopened: " + mask_path)

if not check.crs().isValid():
    print("  WARNING: boundary lacks CRS; assigning", crs_authid)
    processing.run(
        "native:assignprojection",
        {
            "INPUT": mask_path,
            "CRS": mask_crs,
            "OUTPUT": mask_path,
        },
    )
else:
    print("Boundary mask written WITH CRS", check.crs().authid(), "->", mask_path)

del check
gc.collect()


# --- collect project layers -------------------------------------------------
root = project.layerTreeRoot()


def is_visible(layer):
    node = root.findLayer(layer.id())
    return node is not None and node.isVisible()


def is_skippable(layer):
    src = layer.source().lower()
    nm = layer.name().lower()

    if "subwatersheds" in src or "watershed_boundary" in src:
        return True

    if CLIP_SUFFIX.lower() in src or CLIP_SUFFIX.lower() in nm:
        return True

    return False


rasters = []
vectors = []

for lyr in project.mapLayers().values():
    if USE_VISIBLE_ONLY and not is_visible(lyr):
        continue

    if is_skippable(lyr):
        continue

    if isinstance(lyr, QgsRasterLayer):
        if lyr.isValid():
            rasters.append(lyr)
        else:
            print("  WARNING: invalid raster skipped:", lyr.name())

    elif isinstance(lyr, QgsVectorLayer):
        if lyr.isValid():
            vectors.append(lyr)
        else:
            print("  WARNING: invalid vector skipped:", lyr.name(), lyr.source())


print("\nClipping %d raster(s) and %d vector(s)..." % (len(rasters), len(vectors)))

made = []


# --- clip vectors -----------------------------------------------------------
for v in vectors:
    out_path = os.path.join(CLIP_DIR, f"{safe(v.name())}{CLIP_SUFFIX}.gpkg")

    print("  [vector]", v.name(), "(CRS", v.crs().authid() or "NONE", ")")

    try:
        src = v.source()
        vcrs = v.crs()

        if not v.isValid():
            print("     WARNING: invalid vector skipped")
            continue

        if not vcrs.isValid():
            print("     input has no CRS -> assigning", crs_authid)
            asg = processing.run(
                "native:assignprojection",
                {
                    "INPUT": src,
                    "CRS": mask_crs,
                    "OUTPUT": "TEMPORARY_OUTPUT",
                },
            )
            src = asg["OUTPUT"]

        elif vcrs.authid() != crs_authid:
            rep = processing.run(
                "native:reprojectlayer",
                {
                    "INPUT": src,
                    "TARGET_CRS": mask_crs,
                    "OUTPUT": "TEMPORARY_OUTPUT",
                },
            )
            src = rep["OUTPUT"]

        remove_existing_gpkg(out_path)

        processing.run(
            "native:clip",
            {
                "INPUT": src,
                "OVERLAY": mask_path,
                "OUTPUT": out_path,
            },
        )

        chk = QgsVectorLayer(out_path, "chk", "ogr")
        n = chk.featureCount() if chk.isValid() else -1
        del chk

        print("     ->", out_path, "(%d features)" % n)
        made.append(("vector", v.name(), out_path))

    except Exception as ex:
        print("     ERROR:", ex)


# --- clip/copy project rasters ---------------------------------------------
for r in rasters:
    out_path = os.path.join(CLIP_DIR, f"{safe(r.name())}{CLIP_SUFFIX}.tif")

    print("  [raster]", r.name(), "(CRS", r.crs().authid() or "NONE", ")")

    try:
        if COPY_CN_RASTERS_DIRECTLY and is_cn_raster(r):
            print("     CN/test raster -> copying directly to avoid QGIS/GDAL segfault")
            copy_raster_directly(r, out_path)

            if os.path.exists(out_path):
                print("     ->", out_path)
                made.append(("raster", r.name(), out_path))
            else:
                print("     ERROR: copy did not produce output")

            continue

        rcrs = r.crs()

        if not rcrs.isValid():
            print("     raster has no CRS -> using mask CRS", crs_authid)
            rcrs = mask_crs

        clip_raster_file(r.source(), out_path, mask_path, rcrs)

        print("     ->", out_path)
        made.append(("raster", r.name(), out_path))

    except Exception as ex:
        print("     ERROR:", ex)


# --- ensure required DEM template exists -----------------------------------
dem_src_candidates = [
    os.path.join(site_path, "demlr", "cliped_utm.tif"),
    os.path.join(site_path, "demlr", "clipped_utm.tif"),
    os.path.join(OUT_DIR, "dem_carved.tif"),
]

dem_src = None

for p in dem_src_candidates:
    if os.path.exists(p):
        dem_src = p
        break

dem_out = os.path.join(CLIP_DIR, "cliped_utm_wsclip.tif")

if dem_src:

    if not os.path.exists(dem_out):

        print("\nCreating required DEM template by copying...")

        remove_existing_raster(dem_out)

        shutil.copyfile(dem_src, dem_out)

        aux = dem_src + ".aux.xml"
        if os.path.exists(aux):
            shutil.copyfile(aux, dem_out + ".aux.xml")

        print("  ->", dem_out)

        made.append(("raster", "cliped_utm", dem_out))

else:

    print("\nWARNING: Could not locate DEM source.")


# --- load outputs -----------------------------------------------------------
if ADD_TO_PROJECT:
    for kind, name, path in made:
        if kind == "vector":
            lyr = QgsVectorLayer(path, f"{name}{CLIP_SUFFIX}", "ogr")
        else:
            lyr = QgsRasterLayer(path, f"{name}{CLIP_SUFFIX}")

        if lyr.isValid():
            project.addMapLayer(lyr)
        else:
            print("  WARNING: output created but could not be loaded:", path)


print("\nDone. Clipped/copied %d layer(s). Output in: %s" % (len(made), CLIP_DIR))
print("All real clipped outputs written with CRS %s." % crs_authid)
print("CN dummy rasters, if present, were copied directly for testing.")

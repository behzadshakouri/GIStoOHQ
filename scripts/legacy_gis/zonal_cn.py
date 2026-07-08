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
import gc
import processing

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsCoordinateTransformContext,
    QgsField,
    QgsFields,
    QgsFeature,
)

from qgis.PyQt.QtCore import QVariant


# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

SUBWS_NAME = "subwatersheds.gpkg"
SUBWS_LAYER = "subwatersheds"
CN_REL = "clipped/cn.tif"
OUT_NAME = "subwatershed_params.gpkg"
OUT_LAYER = "subwatershed_params"

CN_FIELD = "CN"
CN_DECIMALS = 1
ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------


site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR = os.path.join(site_path, "outputs")
subws = os.path.join(OUT_DIR, SUBWS_NAME)
cn_tif = os.path.join(OUT_DIR, CN_REL)
out_path = os.path.join(OUT_DIR, OUT_NAME)

print("Site  :", site_path)
print("Zones :", subws, "(layer %s)" % SUBWS_LAYER)
print("CN    :", cn_tif)
print("Out   :", out_path)

for p in (subws, cn_tif):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)


# --- helpers ----------------------------------------------------------------
def is_null_value(v):
    if v is None:
        return True

    try:
        if v.isNull():
            return True
    except Exception:
        pass

    return False


def as_float(v):
    if is_null_value(v):
        return None

    try:
        return float(v)
    except Exception:
        pass

    try:
        return float(v.value())
    except Exception:
        return None


def as_int(v, default=None):
    if is_null_value(v):
        return default

    try:
        return int(v)
    except Exception:
        pass

    try:
        return int(v.value())
    except Exception:
        return default


def remove_existing_gpkg(path):
    project = QgsProject.instance()

    for lyr in list(project.mapLayers().values()):
        try:
            if path.lower() in lyr.source().lower():
                project.removeMapLayer(lyr.id())
        except Exception:
            pass

    gc.collect()

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


# --- load subwatersheds -----------------------------------------------------
zones = QgsVectorLayer(subws + "|layername=" + SUBWS_LAYER, "zones_src", "ogr")

if not zones.isValid():
    zones = QgsVectorLayer(subws, "zones_src", "ogr")

if not zones.isValid():
    raise Exception("could not open subwatersheds: " + subws)

print("Subwatersheds:", zones.featureCount())


# --- zonal mean of CN over each polygon -------------------------------------
PREFIX = "cn_"

result = processing.run(
    "native:zonalstatisticsfb",
    {
        "INPUT": zones,
        "INPUT_RASTER": cn_tif,
        "RASTER_BAND": 1,
        "COLUMN_PREFIX": PREFIX,
        "STATISTICS": [2],
        "OUTPUT": "memory:zonal",
    },
)

zonal = result["OUTPUT"]
mean_field = PREFIX + "mean"

if mean_field not in zonal.fields().names():
    raise Exception(
        "zonal statistics did not create expected field '%s'. Fields are: %s"
        % (mean_field, ", ".join(zonal.fields().names()))
    )


# --- build output -----------------------------------------------------------
fields = QgsFields()
fields.append(QgsField("id", QVariant.Int))
fields.append(QgsField("area_km2", QVariant.Double))
fields.append(QgsField(CN_FIELD, QVariant.Double))

remove_existing_gpkg(out_path)

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName = OUT_LAYER

writer = QgsVectorFileWriter.create(
    out_path,
    fields,
    zones.wkbType(),
    zones.crs(),
    QgsCoordinateTransformContext(),
    opts,
)

n = 0
missing = []

zonal_field_names = zonal.fields().names()

for ft in zonal.getFeatures():
    mean = as_float(ft[mean_field])

    fid = as_int(ft["id"], n + 1) if "id" in zonal_field_names else n + 1
    area = as_float(ft["area_km2"]) if "area_km2" in zonal_field_names else None

    of = QgsFeature(fields)
    of.setGeometry(ft.geometry())
    of["id"] = fid

    if area is not None:
        of["area_km2"] = area
    else:
        of["area_km2"] = None

    if mean is None:
        of[CN_FIELD] = None
        missing.append(fid)
    else:
        of[CN_FIELD] = round(mean, CN_DECIMALS)

    writer.addFeature(of)
    n += 1

del writer
gc.collect()


print("\nWrote %d subwatershed(s) -> %s" % (n, OUT_NAME))
print("\n  id    area_km2     CN")

out_lyr = QgsVectorLayer(out_path + "|layername=" + OUT_LAYER, OUT_LAYER, "ogr")

if not out_lyr.isValid():
    out_lyr = QgsVectorLayer(out_path, OUT_LAYER, "ogr")

if not out_lyr.isValid():
    raise Exception("Output was written but could not be reopened: " + out_path)


def sort_key(f):
    fid = as_int(f["id"], None)
    return (fid is None, fid if fid is not None else 10**12)


for ft in sorted(out_lyr.getFeatures(), key=sort_key):
    fid = as_int(ft["id"], None)
    area = as_float(ft["area_km2"])
    cn = as_float(ft[CN_FIELD])

    print(
        "  %-4s  %9s  %5s"
        % (
            str(fid) if fid is not None else "-",
            ("%.4f" % area) if area is not None else "-",
            ("%.1f" % cn) if cn is not None else "NULL",
        )
    )


if missing:
    print("\n*** subwatershed(s) with NULL CN (no classified cells inside):", missing)
    print("    check that cn.tif covers these polygons.")
    print("    also check unmatched NLCD/HSG pairs in buildcnraster.py output.")


if ADD_TO_PROJECT and out_lyr.isValid():
    QgsProject.instance().addMapLayer(out_lyr)
    print("\n  added to project:", OUT_LAYER)


print("\nDone.")

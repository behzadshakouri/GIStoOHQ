# =============================================================================
# extract_soil_texture.py   (QGIS Python Console)
#
# Adds area-weighted sand/silt/clay percentages to subwatershed_params.gpkg,
# computed from the SSURGO-derived texture rasters already produced by
# retrieve_soil_texture.py (ohqbuilder.cli download-texture).
#
# Like extract_slope.py, this APPENDS to the existing subwatershed_params.gpkg
# rather than recreating it: it reads the params layer (already carrying id,
# area_km2, CN, slope_pct, ... from zonal_cn.py / extract_slope.py) and writes
# new sand_pct / silt_pct / clay_pct columns in place. Everything else is
# preserved. Re-running only refreshes these three columns.
#
# Texture source: soils/{sand,silt,clay}_pct.tif, rasterized 0-100 percentage
# fields written by retrieve_soil_texture.py from gSSURGO/SDA component
# horizon data. The zonal mean over each subwatershed polygon is the
# area-weighted texture percentage (equal-area projected raster cells, so the
# cell mean is the area-weighted value) - the same convention used for
# slope_pct.
#
# Inputs (in <SITE>/soils/):
#   sand_pct.tif, silt_pct.tif, clay_pct.tif   from retrieve_soil_texture.py
# Inputs (in <SITE>/outputs/):
#   subwatershed_params.gpkg                   from zonal_cn.py (layer subwatershed_params)
#
# Output: subwatershed_params.gpkg updated in place with columns:
#   sand_pct   area-weighted sand fraction (%)
#   silt_pct   area-weighted silt fraction (%)
#   clay_pct   area-weighted clay fraction (%)
#
# These three columns are read by ohqbuilder.readers.subbasin_reader into
# Subbasin.sand_pct/silt_pct/clay_pct, and used by ohqbuilder.soil_pedotransfer
# (Carsel & Parrish 1988 van Genuchten lookup) when writing the mixed_hru
# .ohq formulation. Sites without this data keep using the generic
# mixed_hydrologic_response_unit.json template defaults.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
import processing
from qgis.core import QgsField, QgsProject, QgsVectorLayer, QgsRasterLayer
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
SOILS_REL = "soils"
PARAMS_NAME = "subwatershed_params.gpkg"
PARAMS_LAYER = "subwatershed_params"

SAND_FIELD = "sand_pct"   # area-weighted sand fraction, percent
SILT_FIELD = "silt_pct"   # area-weighted silt fraction, percent
CLAY_FIELD = "clay_pct"   # area-weighted clay fraction, percent
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------


def value_or_none(value):
    """Convert QGIS NULL/QVariant values to Python None for formatting/math."""

    if value is None:
        return None
    is_null = getattr(value, "isNull", None)
    if callable(is_null) and is_null():
        return None
    wrapped_value = getattr(value, "value", None)
    return wrapped_value() if callable(wrapped_value) else value


def as_float(value):
    value = value_or_none(value)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def id_key(value):
    value = value_or_none(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def format_number(value, pattern, null_text="-"):
    value = value_or_none(value)
    if value is None:
        return null_text
    return pattern % float(value)
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR = os.path.join(site_path, "outputs")
SOILS_DIR = os.path.join(site_path, SOILS_REL)
params = os.path.join(OUT_DIR, PARAMS_NAME)
sand_tif = os.path.join(SOILS_DIR, "sand_pct.tif")
silt_tif = os.path.join(SOILS_DIR, "silt_pct.tif")
clay_tif = os.path.join(SOILS_DIR, "clay_pct.tif")

print("Site   :", site_path)
print("Soils  :", SOILS_DIR)
print("Params :", params)

for p in (params, sand_tif, silt_tif, clay_tif):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

layer = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, "params", "ogr")
if not layer.isValid():
    raise Exception("could not open params layer: " + params)
if not layer.crs().isValid():
    raise Exception("subwatershed_params.gpkg has no valid CRS")
if layer.crs().isGeographic():
    raise Exception(
        "subwatershed_params.gpkg uses geographic CRS %s; projected metres "
        "are required for zonal statistics."
        % layer.crs().authid()
    )

texture_rasters = {
    SAND_FIELD: sand_tif,
    SILT_FIELD: silt_tif,
    CLAY_FIELD: clay_tif,
}

# --- zonal mean sand/silt/clay percentages onto the params layer -----------
texture_by_field = {}
for field, raster_path in texture_rasters.items():
    raster_layer = QgsRasterLayer(raster_path, field + "_crs_check")
    if not raster_layer.isValid():
        raise Exception("texture raster is invalid: " + raster_path)
    if raster_layer.crs() != layer.crs():
        raise Exception(
            "CRS mismatch: %s=%s subwatersheds=%s. Reproject the texture "
            "rasters before running extract_soil_texture.py."
            % (field, raster_layer.crs().authid(), layer.crs().authid())
        )

    prefix = field + "_"
    res = processing.run("native:zonalstatisticsfb", {
        "INPUT": layer,
        "INPUT_RASTER": raster_path,
        "RASTER_BAND": 1,
        "COLUMN_PREFIX": prefix,
        "STATISTICS": [2],          # mean
        "OUTPUT": "memory:" + field,
    })
    zonal = res["OUTPUT"]
    mean_field = prefix + "mean"

    by_id = {}
    for ft in zonal.getFeatures():
        by_id[id_key(ft["id"])] = as_float(ft[mean_field])
    if not any(value is not None for value in by_id.values()):
        raise Exception(
            "%s is NULL for every subwatershed. Verify that %s shares the "
            "params-layer grid/extent and overlaps subwatershed_params.gpkg."
            % (field, os.path.basename(raster_path))
        )
    texture_by_field[field] = by_id
    print(
        "Texture raster: %s | CRS=%s"
        % (os.path.basename(raster_path), raster_layer.crs().authid())
    )

# --- write sand_pct/silt_pct/clay_pct back into the gpkg in place ----------
layer.startEditing()
existing = [f.name() for f in layer.fields()]
to_add = [(SAND_FIELD, QVariant.Double),
          (SILT_FIELD, QVariant.Double),
          (CLAY_FIELD, QVariant.Double)]
new = [QgsField(n, t) for (n, t) in to_add if n not in existing]
if new:
    layer.dataProvider().addAttributes(new)
    layer.updateFields()

field_idx = {field: layer.fields().indexFromName(field) for field in texture_rasters}

for ft in layer.getFeatures():
    key = id_key(ft["id"])
    for field, idx in field_idx.items():
        val = texture_by_field[field].get(key)
        layer.changeAttributeValue(ft.id(), idx,
                                   round(float(val), 3) if val is not None else None)
layer.commitChanges()

# --- report ----------------------------------------------------------------
print("\nUpdated %s with soil texture percentages:" % PARAMS_NAME)
print("\n  id    area_km2     CN   sand_pct   silt_pct   clay_pct")
layer2 = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, PARAMS_LAYER, "ogr")
for ft in sorted(layer2.getFeatures(), key=lambda f: (f["id"] is None, f["id"])):
    cn = ft["CN"] if "CN" in layer2.fields().names() else None
    print("  %-4s  %9s  %5s   %8s   %8s   %8s" % (
        ft["id"],
        format_number(ft["area_km2"], "%.4f"),
        format_number(cn, "%.1f"),
        format_number(ft[SAND_FIELD], "%.2f", "NULL"),
        format_number(ft[SILT_FIELD], "%.2f", "NULL"),
        format_number(ft[CLAY_FIELD], "%.2f", "NULL")))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(layer2)
    print("\n  reloaded:", PARAMS_LAYER)

print("\nDone.")

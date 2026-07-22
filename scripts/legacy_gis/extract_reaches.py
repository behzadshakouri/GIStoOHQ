# =============================================================================
# extract_reaches.py  --  NHA WS3, Stage 12 (HMS network)
#
# Derive routing reaches from the delineation products and attribute them for
# HEC-HMS reach elements. Same ROOT/SITE_DIR + outputs/ + temp/ convention as
# delineatewatershed.py.
#
# METHOD
#   A cell is a CHANNEL cell where upstream flow accumulation exceeds a
#   threshold A_crit. A_crit is set as a FRACTION of the watershed area
#   (scale-invariant across sites), with an optional absolute floor so small
#   sites do not generate a spurious dense network. Channel cells are then
#   traced ALONG flow direction by r.stream.extract -- the reach polyline
#   follows the valley cell-by-cell, it is NOT a straight chord between pour
#   points. The network is split at confluences (r.stream.extract) and,
#   optionally, at each snapped pour point so every HMS junction is a reach
#   break. stream/next_stream topology is carried through for HMS wiring.
#
# CHANNEL GEOMETRY (HMS vs HEC-RAS)
#   HMS routing here uses a SIMPLE trapezoidal section (base width, side slope
#   z, Manning n) supplied as parameters below. Routed hydrographs are
#   relatively insensitive to exact section shape; attenuation is governed
#   mainly by reach length, slope, and roughness. For HEC-RAS we do NOT use
#   this trapezoid -- cross-sections there are cut from the 1 m DEMs to resolve
#   water-surface elevations. The two models intentionally use different
#   geometry sources.
#
# ELEVATIONS
#   flow_dir.tif / flow_acc.tif / dem_carved.tif -> routing only.
#   clipped_utm.tif -> ALL real elevations (reach endpoint Z, slope).
#   The carved surface is an artificial staircase; never use it for slope.
#
# HYDROGRAPHY
#   NHD flowlines are NOT used to define reaches (inconsistent with the DEM).
#   Use them only as a visual cross-check against the extracted network.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import importlib
import importlib.util
import os
import processing
import numpy as np
from osgeo import gdal
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsApplication
)


def _module_spec_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _register_native_provider():
    registry = QgsApplication.processingRegistry()
    if registry.providerById("native") is not None:
        return
    if not _module_spec_available("qgis.analysis"):
        return
    module = importlib.import_module("qgis.analysis")
    provider_class = getattr(module, "QgsNativeAlgorithms")
    registry.addProvider(provider_class())


_register_native_provider()

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

# inputs, relative to <SITE>/outputs/ :
FLOWDIR_REL   = "flow_dir.tif"
FLOWACC_REL   = "flow_acc.tif"           # GRASS r.watershed accumulation (signed)
DEM_REAL_REL  = "cliped_utm_wsclip.tif"  # real elevations, clipped to watershed
WATERSHED_REL = "watershed_boundary.gpkg"   # outer boundary polygon (whole site)
# (no pour points here: reaches are extracted on the WHOLE watershed in phase 1,
#  before interior pour points are placed by hand.)

# --- channel-initiation parameter ------------------------------------------
STREAM_AREA_FRACTION = 0.03      # A_crit = fraction * watershed area
A_CRIT_FLOOR_KM2     = 0.05      # clamp A_crit to this floor; None to disable

SPLIT_AT_JUNCTIONS   = False     # confluence-only for now. r.stream.extract
                                 # splits at confluences + gives stream/next_stream.
                                 # Flip on later only if junctions land mid-reach.

# --- trapezoidal routing parameters (HMS) ----------------------------------
#   Uniform defaults; document basis per site. Swap for a per-reach lookup
#   later if channel classes differ along the network.
BASE_WIDTH_M  = 3.0              # trapezoid bottom width
SIDE_SLOPE_Z  = 2.0             # horizontal:vertical (z H per 1 V)
MANNING_N     = 0.035           # ephemeral natural channel; document source
SLOPE_FLOOR   = 0.0005          # min reach slope, avoids zero/negative

ADD_TO_PROJECT = True
STRICT_CRS = bool(globals().get("STRICT_CRS", True))
# ---------------------------------------------------------------------------

# --- derived paths ---------------------------------------------------------
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
TEMP_DIR  = os.path.join(OUT_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

FLOWDIR_PATH   = os.path.join(OUT_DIR, FLOWDIR_REL)
FLOWACC_PATH   = os.path.join(OUT_DIR, FLOWACC_REL)
DEM_REAL_PATH  = os.path.join(OUT_DIR, "clipped", DEM_REAL_REL)
WATERSHED_PATH = os.path.join(OUT_DIR, WATERSHED_REL)
REACHES_OUT    = os.path.join(OUT_DIR, "reaches.gpkg")

print("Site     :", site_path)
print("Flow acc :", FLOWACC_PATH)
print("Outputs  :", OUT_DIR)

def _require_valid_projected_crs(layer, label):
    if not layer.isValid():
        raise Exception("%s is invalid" % label)
    crs_obj = layer.crs()
    if not crs_obj.isValid():
        raise Exception("%s has no valid CRS" % label)
    if crs_obj.isGeographic():
        raise Exception(
            "%s uses geographic CRS %s; projected metres are required"
            % (label, crs_obj.authid() or crs_obj.description())
        )
    return crs_obj


def _same_crs(a, b):
    return a.isValid() and b.isValid() and a == b


def _raster_signature(path):
    ds = gdal.Open(path)
    if ds is None:
        raise Exception("Could not open raster: " + path)
    gt = ds.GetGeoTransform()
    projection = ds.GetProjection()
    signature = {
        "width": ds.RasterXSize,
        "height": ds.RasterYSize,
        "gt": tuple(gt),
        "projection": projection,
    }
    ds = None
    return signature


def _assert_same_grid(reference_path, other_path, label):
    ref = _raster_signature(reference_path)
    other = _raster_signature(other_path)
    problems = []
    if ref["width"] != other["width"] or ref["height"] != other["height"]:
        problems.append(
            "size %sx%s != %sx%s"
            % (
                other["width"], other["height"],
                ref["width"], ref["height"],
            )
        )
    if any(abs(a - b) > 1.0e-8 for a, b in zip(ref["gt"], other["gt"])):
        problems.append("geotransform differs")
    if ref["projection"] != other["projection"]:
        problems.append("projection differs")
    if problems:
        raise Exception(
            "%s is not grid-aligned with flow_dir.tif: %s"
            % (label, "; ".join(problems))
        )


def grass_id(name):
    reg = QgsApplication.processingRegistry()
    for prefix in ("grass:", "grass7:"):
        alg_id = prefix + name
        if reg.algorithmById(alg_id):
            print("Using GRASS algorithm:", alg_id)
            return alg_id
    if reg.algorithmById("grass:r.watershed"):
        return "grass:" + name
    return "grass7:" + name

# --- read cell size + CRS from flow_dir.tif (spatial source of truth) -------
for _required in (
    FLOWDIR_PATH,
    FLOWACC_PATH,
    DEM_REAL_PATH,
    os.path.join(OUT_DIR, "dem_carved.tif"),
    WATERSHED_PATH,
):
    if not os.path.isfile(_required):
        raise Exception("not found: " + _required)

fdir = QgsRasterLayer(FLOWDIR_PATH, "flow_dir")
crs = _require_valid_projected_crs(fdir, "flow_dir.tif")
px_x = abs(float(fdir.rasterUnitsPerPixelX()))
px_y = abs(float(fdir.rasterUnitsPerPixelY()))
if px_x <= 0 or px_y <= 0:
    raise Exception("flow_dir.tif has invalid pixel size")
cell_area = px_x * px_y
px = (px_x + px_y) / 2.0
print(
    "CRS = %s, pixel = %.4f x %.4f m, cell area = %.3f m^2"
    % (crs.authid(), px_x, px_y, cell_area)
)

for _path, _label in (
    (FLOWACC_PATH, "flow_acc.tif"),
    (os.path.join(OUT_DIR, "dem_carved.tif"), "dem_carved.tif"),
):
    _assert_same_grid(FLOWDIR_PATH, _path, _label)

dem_real_layer = QgsRasterLayer(DEM_REAL_PATH, "real_dem")
dem_real_crs = _require_valid_projected_crs(
    dem_real_layer, "clipped real-elevation DEM"
)
if not _same_crs(crs, dem_real_crs):
    raise Exception(
        "Real DEM CRS mismatch: flow_dir=%s real_dem=%s"
        % (crs.authid(), dem_real_crs.authid())
    )

# --- watershed area -> resolve A_crit -> threshold in cells ----------------
ws = QgsVectorLayer(WATERSHED_PATH, "ws", "ogr")
ws_crs = _require_valid_projected_crs(ws, "watershed boundary")
if not _same_crs(crs, ws_crs):
    print(
        "Reprojecting watershed boundary from %s to %s for reach extraction..."
        % (ws_crs.authid(), crs.authid())
    )
    ws = processing.run(
        "native:reprojectlayer",
        {
            "INPUT": ws,
            "TARGET_CRS": crs,
            "OPERATION": "",
            "OUTPUT": "TEMPORARY_OUTPUT",
        },
    )["OUTPUT"]
    WATERSHED_CLIP_INPUT = ws
else:
    WATERSHED_CLIP_INPUT = WATERSHED_PATH

ws_area_m2  = sum(f.geometry().area() for f in ws.getFeatures())
ws_area_km2 = ws_area_m2 / 1e6

a_crit_km2 = STREAM_AREA_FRACTION * ws_area_km2
if A_CRIT_FLOOR_KM2 is not None and a_crit_km2 < A_CRIT_FLOOR_KM2:
    print(f"  A_crit {a_crit_km2:.4f} km^2 < floor; clamped to {A_CRIT_FLOOR_KM2}")
    a_crit_km2 = A_CRIT_FLOOR_KM2
threshold_cells = max(1, round((a_crit_km2 * 1e6) / cell_area))
print(f"  watershed = {ws_area_km2:.3f} km^2 | fraction = {STREAM_AREA_FRACTION}")
print(f"  -> A_crit = {a_crit_km2:.4f} km^2 -> threshold = {threshold_cells} cells")

# --- channel mask from |flow_acc| (signed; off-region cells negative) ------
#   r.stream.extract can take an accumulation raster directly. We feed it the
#   absolute accumulation so the threshold is on contributing-cell count, and
#   the carved DEM for tie-breaking the trace direction.
acc_abs = os.path.join(TEMP_DIR, "flow_acc_abs.tif")
print("Building |flow_acc| ...")
ds = gdal.Open(FLOWACC_PATH)
gt, proj = ds.GetGeoTransform(), ds.GetProjection()
arr = np.abs(ds.GetRasterBand(1).ReadAsArray().astype("float64"))
drv = gdal.GetDriverByName("GTiff")
out = drv.Create(acc_abs, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32)
out.SetGeoTransform(gt); out.SetProjection(proj)
out.GetRasterBand(1).WriteArray(arr)
out.FlushCache(); out = None; ds = None

# --- extract stream network: accumulation threshold + flow-dir trace -------
print("Running r.stream.extract ...")
se = processing.run(grass_id("r.stream.extract"), {
    "elevation": os.path.join(OUT_DIR, "dem_carved.tif"),
    "accumulation": acc_abs,
    "threshold": threshold_cells,
    "stream_vector": "TEMPORARY_OUTPUT",
    "stream_raster": "TEMPORARY_OUTPUT",
    "direction": "TEMPORARY_OUTPUT",
    "GRASS_REGION_CELLSIZE_PARAMETER": px,
    "GRASS_OUTPUT_TYPE_PARAMETER": 2,    # lines
    "GRASS_VECTOR_DSCO": "", "GRASS_VECTOR_LCO": "",
})
streams = se["stream_vector"]

# --- splitting ---------------------------------------------------------------
#   CONFLUENCE-ONLY (current mode): r.stream.extract already splits the network
#   at every confluence and carries stream/next_stream topology. In a delineated
#   network the snapped pour points sit on the channel and usually coincide with
#   confluences, so confluence splitting alone gives the HMS reaches.
#   After running, overlay reaches.gpkg on pour_points_snapped.gpkg: any junction
#   that lands MID-reach (no confluence there) is one this mode does not break at.
#   If several do, switch SPLIT_AT_JUNCTIONS on (point-split to be added then).
if SPLIT_AT_JUNCTIONS:
    raise NotImplementedError(
        "Point-split not implemented yet. Run confluence-only first "
        "(SPLIT_AT_JUNCTIONS = False), check how many junctions land mid-reach.")
reaches_geom = streams

# --- clip reaches to the watershed boundary --------------------------------
#   r.stream.extract runs over the full DEM extent; keep only the network
#   inside the delineated watershed. Clip (not just select) so segments that
#   cross the boundary are trimmed at it.
print("Clipping reaches to watershed boundary ...")
reaches_geom = processing.run("native:clip", {
    "INPUT": reaches_geom, "OVERLAY": WATERSHED_CLIP_INPUT,
    "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]

# --- attribute: length, endpoint Z (real DEM), slope -----------------------
print("Computing length and sampling real endpoint elevations ...")
reaches_geom = processing.run("native:fieldcalculator", {
    "INPUT": reaches_geom, "FIELD_NAME": "length_m", "FIELD_TYPE": 0,
    "FIELD_LENGTH": 12, "FIELD_PRECISION": 3,
    "FORMULA": "$length", "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]

ends = processing.run("native:extractspecificvertices", {
    "INPUT": reaches_geom, "VERTICES": "0,-1", "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
ends = processing.run("native:rastersampling", {
    "INPUT": ends, "RASTERCOPY": DEM_REAL_PATH,
    "COLUMN_PREFIX": "z", "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]

# build {reach_fid: {0:z_up, -1:z_down}} from sampled endpoints
zlyr = QgsVectorLayer(ends, "ends", "ogr") if isinstance(ends, str) else ends
fields_e = [f.name() for f in zlyr.fields()]
zcol = "z1" if "z1" in fields_e else ("z_1" if "z_1" in fields_e else "z")
idcol = "fid" if "fid" in fields_e else None
zmap = {}
for f in zlyr.getFeatures():
    key = f[idcol] if idcol else f.id()
    zmap.setdefault(key, {})[f["vertex_pos"]] = f[zcol]

# write slope/elev onto reaches via expression using a joined dict is awkward
# in processing; instead compute z_up/z_down/slope in a python edit pass below.
reaches_lyr = QgsVectorLayer(reaches_geom, "reaches", "ogr") \
    if isinstance(reaches_geom, str) else reaches_geom

print("Adding trapezoidal + slope attributes ...")
from qgis.core import QgsField
from qgis.PyQt.QtCore import QVariant
prov = reaches_lyr.dataProvider()
newflds = [
    QgsField("z_up_m",    QVariant.Double),
    QgsField("z_dn_m",    QVariant.Double),
    QgsField("slope_mm",  QVariant.Double),
    QgsField("base_w_m",  QVariant.Double),
    QgsField("side_z",    QVariant.Double),
    QgsField("manning_n", QVariant.Double),
]
existing = [f.name() for f in reaches_lyr.fields()]
prov.addAttributes([f for f in newflds if f.name() not in existing])
reaches_lyr.updateFields()
idx = {n: reaches_lyr.fields().indexOf(n) for n in
       ["z_up_m","z_dn_m","slope_mm","base_w_m","side_z","manning_n","length_m"]}

reaches_lyr.startEditing()
for f in reaches_lyr.getFeatures():
    key = f["fid"] if "fid" in existing else f.id()
    z = zmap.get(key, {})
    z_up = z.get(0); z_dn = z.get(-1)
    L = f["length_m"] or 0.0
    if z_up is not None and z_dn is not None and L > 0:
        slope = max((z_up - z_dn) / L, SLOPE_FLOOR)
    else:
        slope = SLOPE_FLOOR
    upd = {idx["z_up_m"]: z_up, idx["z_dn_m"]: z_dn, idx["slope_mm"]: slope,
           idx["base_w_m"]: BASE_WIDTH_M, idx["side_z"]: SIDE_SLOPE_Z,
           idx["manning_n"]: MANNING_N}
    prov.changeAttributeValues({f.id(): upd})
reaches_lyr.commitChanges()

# --- write reaches.gpkg with CRS explicitly stamped ------------------------
print("Writing", REACHES_OUT, "...")
processing.run("native:assignprojection", {
    "INPUT": reaches_lyr, "CRS": crs, "OUTPUT": REACHES_OUT})

chk = QgsVectorLayer(REACHES_OUT, "reaches_chk", "ogr")
if not chk.isValid():
    raise Exception("reaches.gpkg was written but is invalid")
if chk.crs() != crs:
    raise Exception(
        "reaches.gpkg CRS mismatch: expected %s, got %s"
        % (crs.authid(), chk.crs().authid())
    )
print(f"  reaches written: {chk.featureCount()} | CRS = {chk.crs().authid()}")
if ADD_TO_PROJECT and chk.isValid():
    QgsProject.instance().addMapLayer(chk)

print("\nDone.")
print("CHECK: overlay reaches.gpkg on the NHD blue lines and on")
print("pour_points_snapped.gpkg -- every junction should sit on a reach break,")
print("and the network should roughly track the mapped channels.")
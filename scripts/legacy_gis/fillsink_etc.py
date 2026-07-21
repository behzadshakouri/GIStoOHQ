# =============================================================================
# Hydrology preprocessing with SELECTABLE STREAM-BURNING MODE.
#
# BURN_MODE:
#   "none"      - no burning; r.watershed runs on the raw DEM.
#   "constant"  - lower every channel cell by a constant depth (CONST_DEPTH).
#                 Simple, but can leave reverse gradients where the flowline
#                 and DEM disagree.
#   "synthetic" - overwrite the channel with a strictly-descending synthetic
#                 staircase (ANCHOR_BELOW + DROP_PER_CELL), descending in the
#                 TRUE downhill direction (uphill-digitized lines auto-reversed).
#                 Guarantees no flow-direction reversals in the channel.
#
# In "constant" and "synthetic" modes the CHANNEL cell elevations are altered
# (synthetic ones are fully artificial). Use dem_carved.tif ONLY for
# flow_dir / flow_acc / delineation. Use DEM_PATH for real elevation/slope.
#
# Extra output: channel_elev.gpkg (and channel_elev.tif) -- ONLY the channel
# cells, each carrying its final burned elevation, as points. Lets you inspect
# exactly what the channel profile became.
#
# Runner-overridable paths:
#   ROOT, SITE_DIR, OUT_DIR, DEM_PATH, FLOWLINE_PATH
#
# All outputs go to OUT_DIR, defaulting to <SITE>/outputs/.
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import importlib
import importlib.util
import os
import sys

for plugin_path in (
    "/usr/share/qgis/python/plugins",
    os.path.join(sys.prefix, "share", "qgis", "python", "plugins"),
):
    if os.path.isdir(plugin_path) and plugin_path not in sys.path:
        sys.path.insert(0, plugin_path)

import processing  # noqa: E402
import numpy as np  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
from qgis.core import QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer  # noqa: E402




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


def _register_grass_provider():
    registry = QgsApplication.processingRegistry()
    if registry.algorithmById("grass:r.watershed") or registry.algorithmById("grass7:r.watershed"):
        return
    for plugin_path in (
        "/usr/share/qgis/python/plugins",
        os.path.join(sys.prefix, "share", "qgis", "python", "plugins"),
    ):
        if os.path.isdir(plugin_path) and plugin_path not in sys.path:
            sys.path.insert(0, plugin_path)
    provider_specs = (
        ("grassprovider.Grass7AlgorithmProvider", "Grass7AlgorithmProvider"),
        ("grassprovider.GrassProvider", "GrassProvider"),
        ("processing.algs.grass7.Grass7AlgorithmProvider", "Grass7AlgorithmProvider"),
        ("processing.algs.grass.GrassAlgorithmProvider", "GrassAlgorithmProvider"),
    )
    for module_name, class_name in provider_specs:
        if not _module_spec_available(module_name):
            continue
        module = importlib.import_module(module_name)
        provider_class = getattr(module, class_name)
        provider = provider_class()
        load = getattr(provider, "load", None)
        if load is not None:
            load()
        registry.addProvider(provider)
        if registry.algorithmById("grass:r.watershed") or registry.algorithmById("grass7:r.watershed"):
            return

def _processing_class():
    processing_class = getattr(processing, "Processing", None)
    if processing_class is not None:
        return processing_class
    module_name = "processing.core.Processing"
    if not _module_spec_available(module_name):
        return None
    module = importlib.import_module(module_name)
    return getattr(module, "Processing", None)


def initialize_processing():
    processing_class = _processing_class()
    initialize = getattr(processing_class, "initialize", None)
    if initialize is not None:
        try:
            initialize()
        except Exception:
            pass
    _register_native_provider()
    _register_grass_provider()

ROOT = globals().get(
    "ROOT",
    "/mnt/3rd900/Projects/SligoCreek_QGIS",
)
SITE_DIR = globals().get("SITE_DIR", "")

DEM_REL = globals().get("DEM_REL", os.path.join("demlr", "cliped_utm.tif"))
FLOW_REL = globals().get("FLOW_REL", os.path.join("outputs", "NHDFlowline_clip.gpkg"))

BURN_MODE = globals().get(
    "BURN_MODE",
    "synthetic",
)  # "none" | "constant" | "synthetic"

CONST_DEPTH = float(globals().get("CONST_DEPTH", 10.0))
ANCHOR_BELOW = float(globals().get("ANCHOR_BELOW", 50.0))
DROP_PER_CELL = float(globals().get("DROP_PER_CELL", 0.1))

MAKE_CHANNEL_OUTPUT = bool(globals().get("MAKE_CHANNEL_OUTPUT", True))
ADD_TO_PROJECT = bool(globals().get("ADD_TO_PROJECT", True))

ROOT = os.path.abspath(os.path.expanduser(ROOT))
if os.path.isabs(SITE_DIR):
    site_path = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    site_path = os.path.abspath(os.path.join(ROOT, SITE_DIR))

OUT_DIR = globals().get("OUT_DIR", os.path.join(site_path, "outputs"))
OUT_DIR = os.path.abspath(os.path.expanduser(OUT_DIR))
TEMP_DIR = os.path.join(OUT_DIR, "temp")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

DEM_PATH = globals().get("DEM_PATH", os.path.join(site_path, DEM_REL))
DEM_PATH = os.path.abspath(os.path.expanduser(DEM_PATH))
FLOW_PATH = globals().get(
    "FLOWLINE_PATH",
    globals().get("FLOW_PATH", os.path.join(site_path, FLOW_REL)),
)
FLOW_PATH = os.path.abspath(os.path.expanduser(FLOW_PATH))

carved_path = globals().get("CARVED_DEM_PATH", os.path.join(OUT_DIR, "dem_carved.tif"))
carved_path = os.path.abspath(os.path.expanduser(carved_path))
fdir_path = globals().get("FLOWDIR_PATH", os.path.join(OUT_DIR, "flow_dir.tif"))
fdir_path = os.path.abspath(os.path.expanduser(fdir_path))
facc_path = globals().get("FLOWACC_PATH", os.path.join(OUT_DIR, "flow_acc.tif"))
facc_path = os.path.abspath(os.path.expanduser(facc_path))
chan_pts = globals().get("CHANNEL_POINTS_PATH", os.path.join(OUT_DIR, "channel_elev.gpkg"))
chan_pts = os.path.abspath(os.path.expanduser(chan_pts))
chan_ras = globals().get("CHANNEL_RASTER_PATH", os.path.join(OUT_DIR, "channel_elev.tif"))
chan_ras = os.path.abspath(os.path.expanduser(chan_ras))
flow_utm = os.path.join(TEMP_DIR, "flowlines_utm.gpkg")


def grass_id(name):
    initialize_processing()

    reg = QgsApplication.processingRegistry()

    for prefix in ("grass:", "grass7:"):
        alg_id = prefix + name
        if reg.algorithmById(alg_id):
            print("Using GRASS algorithm:", alg_id)
            return alg_id

    print("Available watershed/GRASS algorithms:")
    for alg in reg.algorithms():
        aid = alg.id()
        if "watershed" in aid.lower() or "grass" in aid.lower():
            print("  ", aid)

    raise Exception(
        "Could not find GRASS watershed algorithm. "
        "Expected grass:r.watershed or grass7:r.watershed. "
        "Install/enable the QGIS GRASS Processing provider (for example qgis-plugin-grass)."
    )


def release_lock(path):
    """Remove any project layer holding path open before overwrite."""
    proj = QgsProject.instance()
    target = os.path.normcase(os.path.abspath(path))
    for lyr in list(proj.mapLayers().values()):
        try:
            src = lyr.source().split("|", 1)[0]
        except Exception:
            src = ""
        if os.path.normcase(os.path.abspath(src)) == target:
            proj.removeMapLayer(lyr.id())


print("Root      :", ROOT)
print("Site      :", site_path)
print("DEM       :", DEM_PATH)
print("Flowlines :", FLOW_PATH)
print("Outputs   :", OUT_DIR)
print("Burn mode :", BURN_MODE)

for path, description in (
    (DEM_PATH, "DEM"),
    (FLOW_PATH, "flowlines"),
):
    if not os.path.isfile(path):
        raise Exception("%s not found: %s" % (description, path))

dem = QgsRasterLayer(DEM_PATH, "dem")
if not dem.isValid():
    raise Exception("DEM invalid / not found: " + DEM_PATH)

dem_crs = dem.crs()
print("DEM CRS:", dem_crs.authid(), "| size:", dem.width(), "x", dem.height())

if BURN_MODE not in ("none", "constant", "synthetic"):
    raise Exception("BURN_MODE must be 'none', 'constant', or 'synthetic'.")

ds = gdal.Open(DEM_PATH)
band = ds.GetRasterBand(1)
gt = ds.GetGeoTransform()
nx, ny = ds.RasterXSize, ds.RasterYSize
elev = band.ReadAsArray().astype("float64")
nodata = band.GetNoDataValue()
proj_wkt = ds.GetProjection()
ox, pxW, _, oy, _, pxH = gt

real = elev.copy()
if nodata is not None:
    real[real == nodata] = np.nan
real_min = np.nanmin(real)

burn = np.full((ny, nx), np.nan, dtype="float64")
chan_mask = np.zeros((ny, nx), dtype=bool)

flow_lyr = QgsVectorLayer(FLOW_PATH, "flow", "ogr")
if not flow_lyr.isValid():
    raise Exception("Flowlines invalid / not found: " + FLOW_PATH)

print("\nFlowlines CRS:", flow_lyr.crs().authid(), "|", flow_lyr.featureCount(), "features")

if flow_lyr.crs().authid() != dem_crs.authid():
    print("  reprojecting flowlines -> %s" % dem_crs.authid())
    processing.run(
        "native:reprojectlayer",
        {
            "INPUT": FLOW_PATH,
            "TARGET_CRS": dem_crs,
            "OUTPUT": flow_utm,
        },
    )
    flow_for_burn = flow_utm
else:
    flow_for_burn = FLOW_PATH


def cell_of(x, y):
    return int((x - ox) / pxW), int((y - oy) / pxH)


def bresenham(c0, r0, c1, r1):
    cells = []
    dc = abs(c1 - c0)
    dr = abs(r1 - r0)
    sc = 1 if c0 < c1 else -1
    sr = 1 if r0 < r1 else -1
    err = dc - dr
    c, r = c0, r0

    while True:
        cells.append((c, r))
        if c == c1 and r == r1:
            break
        e2 = 2 * err
        if e2 > -dr:
            err -= dr
            c += sc
        if e2 < dc:
            err += dc
            r += sr

    return cells


def real_at(c, r):
    if 0 <= c < nx and 0 <= r < ny:
        v = real[r, c]
        return v if not np.isnan(v) else None
    return None


print("\n[1/3] Tracing channel + burning (mode=%s) ..." % BURN_MODE)

vds = ogr.Open(flow_for_burn)
vlyr = vds.GetLayer(0)

n_lines = 0
n_rev = 0

for feat in vlyr:
    geom = feat.GetGeometryRef()
    if geom is None:
        continue

    geoms = []
    if geom.GetGeometryName() == "MULTILINESTRING":
        for k in range(geom.GetGeometryCount()):
            geoms.append(geom.GetGeometryRef(k))
    else:
        geoms.append(geom)

    for line in geoms:
        npts = line.GetPointCount()
        if npts < 2:
            continue

        cellseq = []
        px, py, *_ = line.GetPoint(0)
        c0, r0 = cell_of(px, py)

        for vi in range(1, npts):
            qx, qy, *_ = line.GetPoint(vi)
            c1, r1 = cell_of(qx, qy)
            seg = bresenham(c0, r0, c1, r1)

            if cellseq and seg and seg[0] == cellseq[-1]:
                seg = seg[1:]

            cellseq.extend(seg)
            c0, r0 = c1, r1

        if len(cellseq) < 2:
            continue

        for c, r in cellseq:
            if 0 <= c < nx and 0 <= r < ny:
                chan_mask[r, c] = True

        if BURN_MODE == "constant":
            for c, r in cellseq:
                rv = real_at(c, r)
                if rv is None:
                    continue
                z = rv - CONST_DEPTH
                if np.isnan(burn[r, c]) or z < burn[r, c]:
                    burn[r, c] = z

        elif BURN_MODE == "synthetic":
            head = next((real_at(c, r) for c, r in cellseq if real_at(c, r) is not None), None)
            tail = next((real_at(c, r) for c, r in reversed(cellseq) if real_at(c, r) is not None), None)

            if head is not None and tail is not None and head < tail:
                cellseq.reverse()
                n_rev += 1

            anchor = next((real_at(c, r) for c, r in cellseq if real_at(c, r) is not None), real_min)
            z = anchor - ANCHOR_BELOW

            for c, r in cellseq:
                if 0 <= c < nx and 0 <= r < ny:
                    if np.isnan(burn[r, c]) or z < burn[r, c]:
                        burn[r, c] = z
                z -= DROP_PER_CELL

        n_lines += 1

vds = None

msg = "      traced %d line part(s)" % n_lines
if BURN_MODE == "synthetic":
    msg += "; reversed %d uphill-digitized line(s)" % n_rev
elif BURN_MODE == "none":
    msg += "; no burn applied (channel keeps real DEM elevations)"
print(msg + ".")

out = elev.copy()
mask = ~np.isnan(burn)
out[mask] = burn[mask]

drv = gdal.GetDriverByName("GTiff")

release_lock(carved_path)
cds = drv.Create(carved_path, nx, ny, 1, gdal.GDT_Float32)
cds.SetGeoTransform(gt)
cds.SetProjection(proj_wkt)
cb = cds.GetRasterBand(1)
if nodata is not None:
    cb.SetNoDataValue(nodata)
cb.WriteArray(out.astype("float32"))
cb.FlushCache()
cds = None

print("      carved DEM ->", carved_path)

if MAKE_CHANNEL_OUTPUT and chan_mask.any():
    CHAN_NODATA = -9999.0
    chan_arr = np.full((ny, nx), CHAN_NODATA, dtype="float32")
    chan_arr[chan_mask] = out[chan_mask].astype("float32")

    release_lock(chan_ras)
    rds = drv.Create(chan_ras, nx, ny, 1, gdal.GDT_Float32)
    rds.SetGeoTransform(gt)
    rds.SetProjection(proj_wkt)
    rb = rds.GetRasterBand(1)
    rb.SetNoDataValue(CHAN_NODATA)
    rb.WriteArray(chan_arr)
    rb.FlushCache()
    rds = None

    print("      channel raster ->", chan_ras)

    release_lock(chan_pts)
    if os.path.exists(chan_pts):
        try:
            ogr.GetDriverByName("GPKG").DeleteDataSource(chan_pts)
        except Exception:
            pass

    drv_v = ogr.GetDriverByName("GPKG")
    pds = drv_v.CreateDataSource(chan_pts)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj_wkt)

    play = pds.CreateLayer("channel_elev", srs, ogr.wkbPoint)
    play.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
    play.CreateField(ogr.FieldDefn("col", ogr.OFTInteger))
    play.CreateField(ogr.FieldDefn("row", ogr.OFTInteger))

    defn = play.GetLayerDefn()
    rows, cols = np.where(chan_mask)

    for r, c in zip(rows, cols):
        x = ox + (c + 0.5) * pxW
        y = oy + (r + 0.5) * pxH

        ft = ogr.Feature(defn)
        ft.SetGeometry(ogr.CreateGeometryFromWkt("POINT(%f %f)" % (x, y)))
        ft.SetField("elev", float(out[r, c]))
        ft.SetField("col", int(c))
        ft.SetField("row", int(r))
        play.CreateFeature(ft)
        ft = None

    pds = None
    print("      channel points ->", chan_pts, "(%d cells)" % int(chan_mask.sum()))

ds = None

print("\n[2/3 + 3/3] Flow direction + accumulation (GRASS r.watershed)")

release_lock(fdir_path)
release_lock(facc_path)

alg = grass_id("r.watershed")

params = {
    "elevation": carved_path,
    "drainage": fdir_path,
    "accumulation": facc_path,
    "-s": True,
    "-m": False,
    "-4": False,
    "-a": False,
    "convergence": 5,
    "memory": 1000,
    "GRASS_REGION_PARAMETER": None,
    "GRASS_REGION_CELLSIZE_PARAMETER": 0,
    "GRASS_RASTER_FORMAT_OPT": "",
    "GRASS_RASTER_FORMAT_META": "",
}

try:
    processing.run(alg, params)
except Exception as e:
    print("First GRASS call failed:", e)
    alt_alg = "grass7:r.watershed" if alg == "grass:r.watershed" else "grass:r.watershed"
    print("Trying alternative GRASS algorithm:", alt_alg)
    processing.run(alt_alg, params)

print("      done.")

if ADD_TO_PROJECT:
    proj = QgsProject.instance()

    layers = [
        (carved_path, "dem_carved"),
        (fdir_path, "flow_dir"),
        (facc_path, "flow_acc"),
    ]

    for path, name in layers:
        lyr = QgsRasterLayer(path, name)
        if lyr.isValid():
            proj.addMapLayer(lyr)
            print("  loaded:", name)

    if MAKE_CHANNEL_OUTPUT and chan_mask.any():
        cl = QgsVectorLayer(chan_pts + "|layername=channel_elev", "channel_elev", "ogr")
        if cl.isValid():
            proj.addMapLayer(cl)
            print("  loaded: channel_elev")

print("\nDone. Outputs in:", OUT_DIR)
print("  dem_carved.tif    - DEM used for routing (mode=%s)" % BURN_MODE)
print("  flow_dir.tif      - flow direction")
print("  flow_acc.tif      - flow accumulation")

if MAKE_CHANNEL_OUTPUT:
    print("  channel_elev.tif  - channel cells only, final elevation raster")
    print("  channel_elev.gpkg - channel cells only, final elevation points")

if BURN_MODE != "none":
    print("Reminder: channel elevations are modified; use DEM_PATH for real")
    print("elevation/slope work, not dem_carved.tif:", DEM_PATH)

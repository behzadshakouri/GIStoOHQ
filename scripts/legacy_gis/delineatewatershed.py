# =============================================================================
# Delineate one watershed per pour point, using a single flow-direction raster.
# =============================================================================

import importlib
import importlib.util
import os
import sys
import numpy as np
from osgeo import gdal, ogr, osr

QGIS_PLUGIN_PATH = "/usr/share/qgis/python/plugins"
QGIS_PYTHON_PATH = "/usr/share/qgis/python"

for p in (QGIS_PLUGIN_PATH, QGIS_PYTHON_PATH):
    if p not in sys.path:
        sys.path.insert(0, p)

if "processing" in sys.modules:
    del sys.modules["processing"]

import processing  # noqa: E402


def initialize_processing():
    processing_class = getattr(processing, "Processing", None)
    initialize = getattr(processing_class, "initialize", None)
    if initialize is None:
        return
    try:
        initialize()
    except Exception:
        pass


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


def qgis_run(alg_id, params):
    initialize_processing()
    processing_class = _processing_class()
    run_algorithm = getattr(processing_class, "runAlgorithm", None)
    if run_algorithm is not None:
        result = run_algorithm(alg_id, params)
    else:
        result = processing.run(alg_id, params)
    if result is None:
        raise Exception("Processing algorithm failed: " + alg_id)
    return result


from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsCoordinateTransformContext,
)
from qgis.PyQt.QtCore import QVariant  # noqa: E402

initialize_processing()

try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

FLOWDIR_REL = "flow_dir.tif"
FLOWACC_REL = "flow_acc.tif"
POINTS_REL = "pour_points.shp"

ID_FIELD = None

SNAP = True
SNAP_CELLS = 0

ADD_TO_PROJECT = True

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR = os.path.join(site_path, "outputs")
TEMP_DIR = os.path.join(OUT_DIR, "temp")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

FLOWDIR_PATH = os.path.join(OUT_DIR, FLOWDIR_REL)
FLOWACC_PATH = os.path.join(OUT_DIR, FLOWACC_REL)


def resolve_points(rel):
    candidates = [
        os.path.join(OUT_DIR, rel),
        os.path.join(site_path, rel),
        os.path.join(ROOT, rel),
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return candidates[0]


POINTS_PATH = resolve_points(POINTS_REL)

print("Site        :", site_path)
print("Flow dir    :", FLOWDIR_PATH)
print("Pour points :", POINTS_PATH)
print("Outputs     :", OUT_DIR)


def grass_id(name):
    initialize_processing()

    registry = QgsApplication.processingRegistry()

    for prefix in ("grass:", "grass7:"):
        alg_id = prefix + name
        if registry.algorithmById(alg_id):
            print("Using GRASS algorithm:", alg_id)
            return alg_id

    print("Available watershed/GRASS algorithms:")
    for alg in registry.algorithms():
        aid = alg.id()
        if "watershed" in aid.lower() or "water.outlet" in aid.lower() or "grass" in aid.lower():
            print("  ", aid)

    raise Exception(
        "Could not find GRASS algorithm for %s. Expected grass:%s or grass7:%s. "
        "Install/enable the QGIS GRASS Processing provider (for example qgis-plugin-grass)."
        % (name, name, name)
    )


fdir = QgsRasterLayer(FLOWDIR_PATH, "flow_dir")
if not fdir.isValid():
    raise Exception("Flow direction raster invalid: " + FLOWDIR_PATH)

pts = QgsVectorLayer(POINTS_PATH, "pour_points", "ogr")
if not pts.isValid():
    raise Exception("Pour points layer invalid: " + POINTS_PATH)

print("Points      :", pts.featureCount())

snap_data = None

if SNAP:
    ds = gdal.Open(FLOWACC_PATH)
    if ds is None:
        raise Exception("Could not open flow_acc: " + FLOWACC_PATH)

    band = ds.GetRasterBand(1)
    gt = ds.GetGeoTransform()
    nx, ny = ds.RasterXSize, ds.RasterYSize
    mag = np.abs(band.ReadAsArray().astype("float64"))
    snap_data = (gt, nx, ny, mag)

    print("Snap: ON (window +/-%d cells, highest |flow_acc|)" % SNAP_CELLS)


def snap_point(x, y):
    gt, nx, ny, mag = snap_data

    origin_x, px_w, _, origin_y, _, px_h = gt

    col = int((x - origin_x) / px_w)
    row = int((y - origin_y) / px_h)

    if col < 0 or col >= nx or row < 0 or row >= ny:
        return x, y

    c0 = max(0, col - SNAP_CELLS)
    c1 = min(nx, col + SNAP_CELLS + 1)
    r0 = max(0, row - SNAP_CELLS)
    r1 = min(ny, row + SNAP_CELLS + 1)

    sub = mag[r0:r1, c0:c1]
    ridx, cidx = np.unravel_index(int(np.argmax(sub)), sub.shape)

    best_col = c0 + cidx
    best_row = r0 + ridx

    sx = origin_x + (best_col + 0.5) * px_w
    sy = origin_y + (best_row + 0.5) * px_h

    return sx, sy


print("\nDelineating one watershed per point...")

made = []
field_names = [field.name() for field in pts.fields()]

for i, feat in enumerate(pts.getFeatures(), start=1):
    geom = feat.geometry()

    if geom.isEmpty():
        print("  point %d: empty geometry, skipped" % i)
        continue

    pt = geom.asPoint()
    x, y = pt.x(), pt.y()

    if SNAP:
        sx, sy = snap_point(x, y)
        moved = ((sx - x) ** 2 + (sy - y) ** 2) ** 0.5
        print("  point %d: snapped %.1f m" % (i, moved))
        x, y = sx, sy

    if ID_FIELD and ID_FIELD in field_names:
        tag = str(feat[ID_FIELD])
    else:
        tag = str(i)

    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in tag)

    wat_ras = os.path.join(TEMP_DIR, "wshed_%s.tif" % safe)
    wat_vec = os.path.join(TEMP_DIR, "wshed_%s.gpkg" % safe)
    cleaned = os.path.join(OUT_DIR, "wshed_%s_clean.gpkg" % safe)

    print("  [%d/%d] id='%s' at %.2f,%.2f" % (i, pts.featureCount(), tag, x, y))

    alg = grass_id("r.water.outlet")

    params = {
        "input": FLOWDIR_PATH,
        "coordinates": "%s,%s" % (x, y),
        "output": wat_ras,
        "GRASS_REGION_PARAMETER": None,
        "GRASS_REGION_CELLSIZE_PARAMETER": 0,
        "GRASS_RASTER_FORMAT_OPT": "",
        "GRASS_RASTER_FORMAT_META": "",
    }

    try:
        qgis_run(alg, params)
    except Exception as e:
        print("First GRASS call failed:", e)
        alt_alg = "grass7:r.water.outlet" if alg == "grass:r.water.outlet" else "grass:r.water.outlet"
        print("Trying alternative GRASS algorithm:", alt_alg)
        qgis_run(alt_alg, params)

    for ext in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(wat_vec + ext):
            try:
                os.remove(wat_vec + ext)
            except OSError:
                pass

    src = gdal.Open(wat_ras)
    band = src.GetRasterBand(1)

    srs = osr.SpatialReference()
    srs.ImportFromWkt(src.GetProjection())

    ds_vec = ogr.GetDriverByName("GPKG").CreateDataSource(wat_vec)
    lyr = ds_vec.CreateLayer("wshed", srs=srs, geom_type=ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("DN", ogr.OFTInteger))

    gdal.Polygonize(band, band.GetMaskBand(), lyr, 0, [], callback=None)

    ds_vec = None
    src = None

    poly_layer = QgsVectorLayer(wat_vec + "|layername=wshed", "wshed_%s_poly" % safe, "ogr")

    if not poly_layer.isValid() or poly_layer.featureCount() == 0:
        print("      WARNING: polygonize produced no polygons for id='%s', skipping" % tag)
        continue

    sel = qgis_run(
        "native:extractbyexpression",
        {
            "INPUT": poly_layer,
            "EXPRESSION": '"DN" = 1',
            "OUTPUT": "TEMPORARY_OUTPUT",
        },
    )

    diss = qgis_run(
        "native:dissolve",
        {
            "INPUT": sel["OUTPUT"],
            "FIELD": [],
            "OUTPUT": "TEMPORARY_OUTPUT",
        },
    )

    qgis_run(
        "native:fieldcalculator",
        {
            "INPUT": diss["OUTPUT"],
            "FIELD_NAME": "area_km2",
            "FIELD_TYPE": 0,
            "FIELD_LENGTH": 12,
            "FIELD_PRECISION": 4,
            "FORMULA": "$area / 1000000",
            "OUTPUT": cleaned,
        },
    )

    made.append((tag, cleaned))
    print("      ->", cleaned)


if SNAP and made:
    snapped_path = os.path.join(OUT_DIR, "pour_points_snapped.gpkg")

    fields = QgsFields()
    fields.append(QgsField("id", QVariant.String))

    if os.path.exists(snapped_path):
        try:
            QgsVectorFileWriter.deleteSilently(snapped_path)
        except AttributeError:
            for ext in ("", "-wal", "-shm", "-journal"):
                if os.path.exists(snapped_path + ext):
                    os.remove(snapped_path + ext)

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "pour_points_snapped"

    writer = QgsVectorFileWriter.create(
        snapped_path,
        fields,
        QgsWkbTypes.Point,
        pts.crs(),
        QgsCoordinateTransformContext(),
        opts,
    )

    for i, feat in enumerate(pts.getFeatures(), start=1):
        pt = feat.geometry().asPoint()
        sx, sy = snap_point(pt.x(), pt.y())

        f2 = QgsFeature(fields)
        f2.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(sx, sy)))
        f2["id"] = str(i)

        writer.addFeature(f2)

    del writer

    print("\nSnapped points ->", snapped_path)


if ADD_TO_PROJECT:
    proj = QgsProject.instance()

    for tag, path in made:
        lyr = QgsVectorLayer(path, "wshed_%s" % tag, "ogr")
        if lyr.isValid():
            proj.addMapLayer(lyr)


print("\nDone. %d watershed(s) in %s" % (len(made), OUT_DIR))
print("Scratch rasters are in temp/ (safe to delete).")
print("If a watershed does not climb the channel after snapping, the channel")
print("is not continuously downhill there -- in fillsink_etc.py use")
print("BURN_MODE='synthetic' (guarantees descent), regenerate flow_dir, re-run.")

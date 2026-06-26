# =============================================================================
# Build non-overlapping subwatersheds from the nested/tributary watershed
# polygons (wshed_*_clean.gpkg from delineatewatershed.py).
#
# Logic (area-based, geometry-driven -- no manual ordering needed):
#   - Sort watersheds smallest -> largest.
#   - For each, subtract the union of every STRICTLY SMALLER watershed that it
#     actually contains/overlaps (>= CONTAIN_FRAC).
#   - Clean: snap to the raster grid + drop stray sliver fragments left by
#     pixel-edge subtraction.
#
# Inputs from <SITE>/outputs/. Output subwatersheds.gpkg to <SITE>/outputs/.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import os
import glob
import processing
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsField, QgsFields,
    QgsGeometry, QgsVectorFileWriter, QgsWkbTypes, QgsCoordinateTransformContext,
    QgsCoordinateReferenceSystem
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

WSHED_GLOB   = "wshed_*_clean.gpkg"      # in <SITE>/outputs/
CONTAIN_FRAC = 0.90          # FALLBACK only: used when a shed's pour point is
                             # unavailable. Nesting is decided by pour-point-
                             # inside-polygon (binary, robust), not this frac.
POURPTS_NAME = "pour_points_snapped.gpkg"   # id-keyed, from delineatewatershed

# DEM is the CRS source of truth for the site. Used to guarantee the output
# carries a CRS even if an input watershed somehow lost it.
DEM_REL      = "demlr/cliped_utm.tif"

# --- sliver cleanup (cell size ~9.34 m here, so 1 cell ~= 87 m2) -----------
MIN_AREA_M2  = 500.0         # drop stray fragments smaller than this (~6 cells)
SNAP_GRID    = 9.336         # snap vertices to this grid (= DEM cell size);
                             # set to 0 to disable
# ---------------------------------------------------------------------------

# --- derived paths ---------------------------------------------------------
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
os.makedirs(OUT_DIR, exist_ok=True)
WSHED_DIR = OUT_DIR
OUT_PATH  = os.path.join(OUT_DIR, "subwatersheds.gpkg")
DEM_PATH  = os.path.join(site_path, DEM_REL)

print("Site     :", site_path)
print("Watershed dir:", WSHED_DIR)
print("Output   :", OUT_PATH)

# the authoritative site CRS, read from the DEM
dem_crs = None
_dem = QgsRasterLayer(DEM_PATH, "dem")
if _dem.isValid() and _dem.crs().isValid():
    dem_crs = _dem.crs()
    print("DEM CRS  :", dem_crs.authid())
else:
    print("  (could not read DEM CRS from", DEM_PATH, "- will use input CRS)")

# --- load all watershed polygons -------------------------------------------
files = sorted(glob.glob(os.path.join(WSHED_DIR, WSHED_GLOB)))
if not files:
    raise Exception("No files matching %s in %s" % (WSHED_GLOB, WSHED_DIR))

print("\nFound %d watershed file(s):" % len(files))
sheds = []
crs = None
for f in files:
    lyr = QgsVectorLayer(f, os.path.basename(f), "ogr")
    if not lyr.isValid() or lyr.featureCount() == 0:
        print("  SKIP (invalid/empty):", f)
        continue
    if crs is None:
        crs = lyr.crs()
    geoms = [ft.geometry() for ft in lyr.getFeatures() if not ft.geometry().isEmpty()]
    if not geoms:
        print("  SKIP (no geometry):", f)
        continue
    g = QgsGeometry.unaryUnion(geoms)
    if SNAP_GRID and SNAP_GRID > 0:
        g = g.snappedToGrid(SNAP_GRID, SNAP_GRID)
        g = g.makeValid()
    tag = os.path.basename(f).replace("wshed_", "").replace("_clean.gpkg", "")
    sheds.append({"id": tag, "geom": g, "area": g.area(), "file": f})
    print("  loaded id='%s'  area=%.4f km2" % (tag, g.area() / 1e6))

if not sheds:
    raise Exception("No valid watershed polygons loaded.")

# --- load pour points (id -> point geom) for the nesting test --------------
# Nesting is "B is upstream of / nested in A iff B's pour point lies inside A".
# This is binary and robust, unlike an area-fraction threshold that fails for
# children that sit only ~85-92% inside their parent at a confluence.
pourpts_p = os.path.join(OUT_DIR, POURPTS_NAME)
pp_geom = {}    # id (str) -> QgsGeometry (point)
_pp = QgsVectorLayer(pourpts_p, "pp", "ogr")
if _pp.isValid():
    pp_fields = [f.name() for f in _pp.fields()]
    id_field = "id" if "id" in pp_fields else None
    for ft in _pp.getFeatures():
        g = ft.geometry()
        if g is None or g.isEmpty():
            continue
        tag = str(ft[id_field]) if id_field else str(ft.id())
        pp_geom[tag] = QgsGeometry(g)
    print("Pour points loaded for nesting test:", len(pp_geom))
else:
    print("  WARNING: %s not found -- falling back to area-fraction nesting "
          "test (CONTAIN_FRAC=%.2f)" % (pourpts_p, CONTAIN_FRAC))

# choose the output CRS: prefer the DEM's, fall back to the first input's
out_crs = dem_crs if (dem_crs is not None and dem_crs.isValid()) else crs
if out_crs is None or not out_crs.isValid():
    raise Exception("No valid CRS available (DEM and inputs both lack one).")
print("Output CRS:", out_crs.authid())

sheds.sort(key=lambda s: s["area"])

def drop_slivers(geom, min_area):
    if geom.isEmpty():
        return geom
    parts = []
    if geom.isMultipart():
        for part in geom.asGeometryCollection():
            if part.area() >= min_area:
                parts.append(part)
    else:
        if geom.area() >= min_area:
            parts.append(geom)
    if not parts:
        return QgsGeometry()
    out = parts[0]
    for p in parts[1:]:
        out = out.combine(p)
    return out

print("\nCarving non-overlapping subwatersheds...")
for i, s in enumerate(sheds):
    larger_geom = s["geom"]
    to_subtract = []
    for j in range(i):
        small = sheds[j]
        # primary test: is the SMALLER shed's pour point inside the larger shed?
        # (binary, robust -- defines true upstream nesting)
        pp = pp_geom.get(small["id"])
        nested = None
        if pp is not None:
            nested = larger_geom.contains(pp)
        if nested is None:
            # fallback: area-fraction containment when no pour point available
            inter = larger_geom.intersection(small["geom"])
            if inter.isEmpty():
                nested = False
            else:
                frac = inter.area() / small["area"] if small["area"] > 0 else 0.0
                nested = frac >= CONTAIN_FRAC
        if nested:
            to_subtract.append(small["geom"])
    if to_subtract:
        cut = QgsGeometry.unaryUnion(to_subtract)
        sub = larger_geom.difference(cut)
        n = len(to_subtract)
    else:
        sub = larger_geom
        n = 0
    sub = sub.makeValid()
    sub = drop_slivers(sub, MIN_AREA_M2)
    s["sub"] = sub
    if sub.isEmpty():
        print("  id='%s': EMPTY after subtraction+cleanup -- points likely too "
              "close; consider removing this point" % s["id"])
    else:
        print("  id='%s': subtracted %d nested, area now %.4f km2"
              % (s["id"], n, sub.area() / 1e6))

# --- write combined output -------------------------------------------------
fields = QgsFields()
fields.append(QgsField("id", QVariant.String))
fields.append(QgsField("area_km2", QVariant.Double))

if os.path.exists(OUT_PATH):
    try:
        QgsVectorFileWriter.deleteSilently(OUT_PATH)
    except AttributeError:
        for ext in ("", "-wal", "-shm", "-journal"):
            p = OUT_PATH + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as e:
                    print("  WARNING: could not remove", p, "-", e)

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName = "subwatersheds"
writer = QgsVectorFileWriter.create(
    OUT_PATH, fields, QgsWkbTypes.MultiPolygon, out_crs,
    QgsCoordinateTransformContext(), opts)

written = 0
for s in sheds:
    geom = s["sub"]
    if geom.isEmpty():
        continue
    if geom.wkbType() not in (QgsWkbTypes.MultiPolygon, QgsWkbTypes.Polygon):
        coerced = geom.coerceToType(QgsWkbTypes.MultiPolygon)
        if coerced:
            geom = coerced[0]
    feat = QgsFeature(fields)
    feat.setGeometry(geom)
    feat["id"] = s["id"]
    feat["area_km2"] = round(geom.area() / 1e6, 4)
    writer.addFeature(feat)
    written += 1

del writer
print("\nWrote %d subwatershed(s) -> %s" % (written, OUT_PATH))

# guarantee the CRS is on the file (stamp it if the build dropped it)
_chk = QgsVectorLayer(OUT_PATH + "|layername=subwatersheds", "c", "ogr")
if not (_chk.isValid() and _chk.crs().isValid()):
    print("  output lacked CRS -> stamping", out_crs.authid())
    processing.run("native:assignprojection",
                   {"INPUT": OUT_PATH + "|layername=subwatersheds",
                    "CRS": out_crs, "OUTPUT": OUT_PATH})
    _chk = QgsVectorLayer(OUT_PATH + "|layername=subwatersheds", "c", "ogr")
print("Output CRS on file:", _chk.crs().authid() or "NONE")

total = sum(s["sub"].area() for s in sheds if not s["sub"].isEmpty())
print("Total subwatershed area: %.4f km2" % (total / 1e6))

# --- self-check: no two carved subwatersheds should overlap > tolerance ----
OVERLAP_TOL_M2 = 1000.0
carved = [(s["id"], s["sub"]) for s in sheds if not s["sub"].isEmpty()]
overlaps = []
for a in range(len(carved)):
    ida, ga = carved[a]
    for b in range(a + 1, len(carved)):
        idb, gb = carved[b]
        inter = ga.intersection(gb)
        if not inter.isEmpty() and inter.area() > OVERLAP_TOL_M2:
            overlaps.append((ida, idb, inter.area()))
if overlaps:
    print("\n  *** RESIDUAL OVERLAPS (subtraction incomplete) ***")
    for ida, idb, ar in overlaps:
        print("      id %s overlaps id %s by %.4f km2" % (ida, idb, ar / 1e6))
    print("  Check that both sheds have a pour point in pour_points_snapped.gpkg")
    print("  and that the nested one's pour point falls inside the parent.")
else:
    print("Self-check: no residual overlaps > %.0f m2. Subwatersheds are clean."
          % OVERLAP_TOL_M2)

# --- load into project (remove any stale layer pointing at this file first) -
proj = QgsProject.instance()
for lyr in list(proj.mapLayers().values()):
    try:
        if os.path.normpath(OUT_PATH) in os.path.normpath(lyr.source()):
            proj.removeMapLayer(lyr.id())
    except Exception:
        pass

out_lyr = QgsVectorLayer(OUT_PATH + "|layername=subwatersheds",
                         "subwatersheds", "ogr")
if out_lyr.isValid():
    proj.addMapLayer(out_lyr)
    print("Loaded 'subwatersheds' (%d features) into the project."
          % out_lyr.featureCount())
else:
    print("WARNING: subwatersheds layer did not load -- open it manually:")
    print("  ", OUT_PATH)

print("\nDone. Output in:", OUT_DIR)
print("Sliver fragments below %.0f m2 dropped; geometries snapped to %.3f m grid."
      % (MIN_AREA_M2, SNAP_GRID))
print("If a subwatershed came out EMPTY, two pour points were too close --")
print("remove one and re-run delineation + this script.")

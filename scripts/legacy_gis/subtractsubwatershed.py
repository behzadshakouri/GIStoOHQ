# =============================================================================
# Build non-overlapping subwatersheds from the nested/tributary watershed
# polygons (wshed_*_clean.gpkg from delineatewatershed.py).
#
# Logic (area-based, geometry-driven -- no manual ordering needed):
#   - Treat wshed_*_clean.gpkg as cumulative upstream drainage areas.  It is
#     normal for these inputs to overlap and for a downstream one to equal the
#     sum of several upstream ones plus its local drainage area.
#   - Build an explicit containment tree from polygon coverage.
#   - Subtract only each watershed's immediate children.  The resulting
#     subwatersheds.gpkg is an incremental, non-overlapping partition.
#   - Clean: snap to the raster grid + drop stray sliver fragments left by
#     pixel-edge subtraction.
#
# Inputs from <SITE>/outputs/. Output subwatersheds.gpkg to <SITE>/outputs/.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import os
import glob
import json
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
CONTAIN_FRAC = 0.90          # minimum fraction of a child covered by a parent
POURPTS_NAME = "pour_points_snapped.gpkg"   # id-keyed, from delineatewatershed

# DEM is the CRS source of truth for the site. Used to guarantee the output
# carries a CRS even if an input watershed somehow lost it.
DEM_REL      = "demlr/cliped_utm.tif"

# --- sliver cleanup (cell size ~9.34 m here, so 1 cell ~= 87 m2) -----------
MIN_AREA_M2 = 1e6 * float(globals().get("MIN_SUBWATERSHED_AREA_KM2", 0.0005))
SNAP_GRID    = 9.336         # snap vertices to this grid (= DEM cell size);
                             # set to 0 to disable
OVERLAP_TOL_M2 = 250.0       # fail above roughly three 9.34 m raster cells
CROSSING_TOL_M2 = 1000.0     # fail if cumulative basins cross without nesting
# ---------------------------------------------------------------------------

# --- derived paths ---------------------------------------------------------
site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
os.makedirs(OUT_DIR, exist_ok=True)
WSHED_DIR = OUT_DIR
OUT_PATH  = os.path.join(OUT_DIR, "subwatersheds.gpkg")
REPORT_PATH = os.path.join(OUT_DIR, "subwatershed_partition_report.json")
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

# --- load pour points (id -> point geom) for diagnostics --------------------
# Pour points are not used to infer nesting.  A child's outlet can lie exactly
# on a parent's rasterized boundary, so contains(point) is directionally and
# numerically unreliable.  Polygon coverage defines the hierarchy instead.
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
    print("Pour points loaded for hierarchy diagnostics:", len(pp_geom))
else:
    print("  NOTE: %s not found; polygon coverage will still define hierarchy."
          % pourpts_p)

# choose the output CRS: prefer the DEM's, fall back to the first input's
out_crs = dem_crs if (dem_crs is not None and dem_crs.isValid()) else crs
if out_crs is None or not out_crs.isValid():
    raise Exception("No valid CRS available (DEM and inputs both lack one).")
print("Output CRS:", out_crs.authid())

sheds.sort(key=lambda s: s["area"])

# --- construct the cumulative-watershed containment tree -------------------
# The immediate parent is the smallest larger polygon covering at least
# CONTAIN_FRAC of the child.  This avoids subtracting every descendant more
# than once and makes the relationship available for QA and inspection.
for s in sheds:
    s["parent"] = None
    s["children"] = []

for i, child in enumerate(sheds):
    candidates = []
    for parent in sheds[i + 1:]:
        inter = child["geom"].intersection(parent["geom"])
        inter_area = 0.0 if inter.isEmpty() else inter.area()
        child_frac = inter_area / child["area"] if child["area"] > 0 else 0.0
        parent_frac = inter_area / parent["area"] if parent["area"] > 0 else 0.0

        # Near-identical cumulative basins mean duplicate/mis-snapped outlets.
        if child_frac >= 0.995 and parent_frac >= 0.995:
            raise Exception(
                "Cumulative watersheds %s and %s are effectively identical. "
                "Their pour points are probably on the same routed cell; remove "
                "or move one point." % (child["id"], parent["id"])
            )
        if child_frac >= CONTAIN_FRAC:
            candidates.append((parent["area"], parent, child_frac))
        elif inter_area > CROSSING_TOL_M2:
            raise Exception(
                "Cumulative watersheds %s and %s cross by %.4f km2 but neither "
                "contains the other. Check pour-point snapping and flow direction."
                % (child["id"], parent["id"], inter_area / 1e6)
            )

    if candidates:
        _, parent, coverage = min(candidates, key=lambda item: item[0])
        child["parent"] = parent
        parent["children"].append(child)
        print("  hierarchy: %s -> parent %s (coverage %.1f%%)"
              % (child["id"], parent["id"], coverage * 100.0))

roots = [s for s in sheds if s["parent"] is None]
if len(roots) != 1:
    raise Exception(
        "Expected one downstream/root cumulative watershed, found %d (%s). "
        "The pour points form disconnected or inconsistent drainage trees."
        % (len(roots), ", ".join(s["id"] for s in roots))
    )
root_shed = roots[0]
print("Cumulative watershed root:", root_shed["id"])

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
assigned_parts = []
for i, s in enumerate(sheds):
    larger_geom = s["geom"]
    to_subtract = [child["geom"] for child in s["children"]]
    if to_subtract:
        cut = QgsGeometry.unaryUnion(to_subtract)
        sub = larger_geom.difference(cut)
        n = len(to_subtract)
    else:
        sub = larger_geom
        n = 0
    sub = sub.makeValid()
    # Raster polygonization and per-basin grid snapping can leave narrow shared
    # strips even when the containment tree is correct. Give previously carved
    # upstream units priority and remove their union from every subsequent unit.
    # This makes disjointness a construction invariant, not merely a post-check.
    if assigned_parts and not sub.isEmpty():
        sub = sub.difference(QgsGeometry.unaryUnion(assigned_parts)).makeValid()
    sub = drop_slivers(sub, MIN_AREA_M2)
    s["sub"] = sub
    if sub.isEmpty():
        print("  id='%s': EMPTY after subtraction+cleanup -- points likely too "
              "close; consider removing this point" % s["id"])
    else:
        assigned_parts.append(sub)
        print("  id='%s': subtracted %d immediate child(ren), area now %.4f km2"
              % (s["id"], n, sub.area() / 1e6))

empty_ids = [s["id"] for s in sheds if s["sub"].isEmpty()]
if empty_ids:
    raise Exception(
        "Subwatershed partition contains empty unit(s): %s. The associated "
        "pour points are duplicate, too close, or snapped to inconsistent cells."
        % ", ".join(empty_ids)
    )

# --- write combined output -------------------------------------------------
fields = QgsFields()
fields.append(QgsField("id", QVariant.String))
fields.append(QgsField("area_km2", QVariant.Double))
fields.append(QgsField("raw_km2", QVariant.Double))
fields.append(QgsField("parent_id", QVariant.String))
fields.append(QgsField("child_cnt", QVariant.Int))

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
    feat["raw_km2"] = round(s["area"] / 1e6, 4)
    feat["parent_id"] = s["parent"]["id"] if s["parent"] is not None else ""
    feat["child_cnt"] = len(s["children"])
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

# --- strict partition QA ---------------------------------------------------
carved = [(s["id"], s["sub"]) for s in sheds if not s["sub"].isEmpty()]
overlaps = []
pairwise_overlap = []
for a in range(len(carved)):
    ida, ga = carved[a]
    for b in range(a + 1, len(carved)):
        idb, gb = carved[b]
        inter = ga.intersection(gb)
        overlap_area = 0.0 if inter.isEmpty() else inter.area()
        pairwise_overlap.append({
            "left_id": ida, "right_id": idb,
            "overlap_m2": round(overlap_area, 3),
        })
        if overlap_area > OVERLAP_TOL_M2:
            overlaps.append((ida, idb, overlap_area))
# The union of incremental pieces must reproduce the downstream/root basin.
# Allow dropped slivers, but never silently accept a material gap or area
# outside the root cumulative watershed.
carved_union = QgsGeometry.unaryUnion([g for _, g in carved])
gap = root_shed["geom"].difference(carved_union)
outside = carved_union.difference(root_shed["geom"])
gap_area = 0.0 if gap.isEmpty() else gap.area()
outside_area = 0.0 if outside.isEmpty() else outside.area()
coverage_tol = max(5000.0, MIN_AREA_M2 * len(sheds))
print("Partition coverage: gap=%.0f m2, outside=%.0f m2 (tolerance %.0f m2)"
      % (gap_area, outside_area, coverage_tol))

report = {
    "status": (
        "pass"
        if not overlaps and gap_area <= coverage_tol and outside_area <= coverage_tol
        else "fail"
    ),
    "pour_point_method": "Phase 1 junction points snapped to routed cells",
    "pour_point_path": pourpts_p,
    "pour_point_count": len(pp_geom),
    "root_id": root_shed["id"],
    "overlap_tolerance_m2": OVERLAP_TOL_M2,
    "maximum_pairwise_overlap_m2": max(
        [item["overlap_m2"] for item in pairwise_overlap], default=0.0
    ),
    "gap_m2": round(gap_area, 3),
    "outside_m2": round(outside_area, 3),
    "coverage_tolerance_m2": coverage_tol,
    "subwatersheds": [
        {
            "id": s["id"],
            "parent_id": s["parent"]["id"] if s["parent"] is not None else None,
            "child_ids": [child["id"] for child in s["children"]],
            "cumulative_area_km2": round(s["area"] / 1e6, 6),
            "incremental_area_km2": round(s["sub"].area() / 1e6, 6),
        }
        for s in sheds
    ],
    "pairwise_overlaps": pairwise_overlap,
    "checks": {
        "overlap_within_tolerance": not overlaps,
        "gap_within_tolerance": gap_area <= coverage_tol,
        "outside_within_tolerance": outside_area <= coverage_tol,
    },
}
with open(REPORT_PATH, "w", encoding="utf-8") as report_file:
    json.dump(report, report_file, indent=2)
    report_file.write("\n")
print("Partition QA report:", REPORT_PATH)
if overlaps:
    print("\n  *** RESIDUAL OVERLAPS (subtraction incomplete) ***")
    for ida, idb, ar in overlaps:
        print("      id %s overlaps id %s by %.4f km2" % (ida, idb, ar / 1e6))
    raise Exception(
        "Subwatershed partition validation failed: residual overlap exceeds "
        "%.0f m2. Check pour-point snapping and cumulative watershed hierarchy."
        % OVERLAP_TOL_M2
    )
else:
    print("Self-check: no residual overlaps > %.0f m2. Subwatersheds are clean."
          % OVERLAP_TOL_M2)
if gap_area > coverage_tol or outside_area > coverage_tol:
    raise Exception(
        "Subwatershed partition does not reproduce root watershed %s: "
        "gap=%.0f m2, outside=%.0f m2. Check duplicate/crossing pour points "
        "and rerun delineation." %
        (root_shed["id"], gap_area, outside_area)
    )

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

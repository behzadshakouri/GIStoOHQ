# =============================================================================
# ras_build_terrain.py   (QGIS Python Console)
#
# HEC-RAS PIPELINE -- STEP 1: assemble a single, RAS-ready 1 m terrain for ONE
# reach centered on the AZ12-100 site, with NO human intervention.
#
# DOMAIN DEFINITION (the key design choice):
#   * The reach centerline is the NHD flowline (a surveyed hydrography product,
#     independent of DEM resolution -- so it does NOT inherit the coarse-grid
#     flow-accumulation path the HMS reaches were traced on).
#   * NHDFlowline_clip covers the WHOLE watershed; we need only one reach. So we
#     snap the site point (point/) to the channel and trace a fixed channel
#     distance UPSTREAM and DOWNSTREAM along the main stem:
#         LEN_UPSTREAM_M   (800 m)  -- the reach of interest (flood comes from
#                                      upstream; this is what we map)
#         LEN_DOWNSTREAM_M (400 m)  -- carried PAST the site so the downstream
#                                      normal-depth BC sits well below the area
#                                      of interest (standard 1D practice) and to
#                                      capture any downstream control/backwater.
#   * At upstream confluences the trace follows the MAIN STEM (longest branch),
#     giving a single non-branching reach. Downstream is single-path.
#   * The traced reach is buffered HALF_WIDTH_M (200 m) each side; the 1 m DEM is
#     clipped to that corridor's BOUNDING BOX (clean rectangle, so later
#     cross-section cut lines always have continuous terrain).
#
# INPUT
#   <SITE>/demhr/*.tif                     native 1 m USGS 3DEP tiles
#   <SITE>/outputs/NHDFlowline_clip.gpkg   clipped NHD flowlines (whole watershed)
#   <SITE>/point/AZ12-100.shp (or *.shp)   site location point
#
# OUTPUT  (all in <SITE>/outputs_RAS/)
#   demhr_merged_utm.tif    merged + reprojected 1 m DEM (full two-tile extent)
#   site_on_channel.gpkg    the snapped site point (INSPECT the snap distance!)
#   reach_centerline.gpkg   the traced single reach (main stem, 800 up / 400 down)
#   reach_corridor.gpkg     the 200 m buffer polygon
#   ras_terrain.tif         <-- the RAS terrain: 1 m DEM clipped to corridor bbox
#
# Run from: QGIS -> Plugins -> Python Console.
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   exec(open(SCRIPT_DIR + "/ras_build_terrain.py").read())
# =============================================================================
import os
import glob
import math
import processing
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsVectorLayer, QgsFeature,
    QgsGeometry, QgsPointXY, QgsField, QgsFields,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant

# --- root resolution -------------------------------------------------------
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

# --- settings --------------------------------------------------------------
DEMHR_SUBDIR     = "demhr"
FLOWLINE_REL     = "outputs/NHDFlowline_clip.gpkg"
POINT_SUBDIR     = "point"               # site point lives here; first *.shp used
POINT_NAME       = None                  # None -> first .shp in point/; or "AZ12-100.shp"

LEN_UPSTREAM_M   = 800.0                  # trace distance up-channel from the site
LEN_DOWNSTREAM_M = 400.0                  # trace distance down-channel past the site
HALF_WIDTH_M     = 200.0                  # corridor half-width each side

# --- main-stem selection ---------------------------------------------------
# The site point sits on a minor tributary, but the reach we MODEL is the main
# stem it drains into. The main-stem segments are labeled in NHDFlowline_clip
# with NAME_FIELD == MAIN_STEM_NAME (here: gnis_name == "main", set by hand).
# The site snaps to the nearest node ON a main-stem segment within SNAP_RADIUS_M;
# at confluences the trace stays on same-named segments (geometry fallback if a
# fork has no labeled continuation).
MAIN_STEM_NAME   = "main"                 # the gnis_name you assigned to the main stem
SNAP_RADIUS_M    = 500.0                  # search radius for the main stem
NAME_FIELD       = "gnis_name"

TARGET_EPSG      = None                   # None = auto UTM (AZ12-100 -> 26912)
RESAMPLING       = 1                      # bilinear
NODATA           = -9999
FALLBACK_EPSG    = 26912
ADD_TO_PROJECT   = True

MERGED_NAME      = "demhr_merged_utm.tif"
SNAP_NAME        = "site_on_channel.gpkg"
CENTERLINE_NAME  = "reach_centerline.gpkg"
CORRIDOR_NAME    = "reach_corridor.gpkg"
TERRAIN_NAME     = "ras_terrain.tif"
# ---------------------------------------------------------------------------

project   = QgsProject.instance()
site_path = os.path.join(ROOT, SITE_DIR)
DEMHR_DIR = os.path.join(site_path, DEMHR_SUBDIR)
OUT_DIR   = os.path.join(site_path, "outputs_RAS")
os.makedirs(OUT_DIR, exist_ok=True)

FLOWLINE_PATH   = os.path.join(site_path, FLOWLINE_REL)
MERGED_PATH     = os.path.join(OUT_DIR, MERGED_NAME)
SNAP_PATH       = os.path.join(OUT_DIR, SNAP_NAME)
CENTERLINE_PATH = os.path.join(OUT_DIR, CENTERLINE_NAME)
CORRIDOR_PATH   = os.path.join(OUT_DIR, CORRIDOR_NAME)
TERRAIN_PATH    = os.path.join(OUT_DIR, TERRAIN_NAME)

print("=" * 70)
print("HEC-RAS STEP 1 -- terrain assembly (single reach)")
print("  Site        :", site_path)
print("  up / down   : %.0f m / %.0f m" % (LEN_UPSTREAM_M, LEN_DOWNSTREAM_M))
print("  half-width  : %.0f m" % HALF_WIDTH_M)
print("  out dir     :", OUT_DIR)
print("=" * 70)

# --- preflight -------------------------------------------------------------
tiles = sorted(glob.glob(os.path.join(DEMHR_DIR, "*.tif")))
if not tiles:
    raise Exception("No .tif tiles in " + DEMHR_DIR)
print("DEM tiles (%d):" % len(tiles))
for t in tiles:
    print("   ", os.path.basename(t))
if not os.path.isfile(FLOWLINE_PATH):
    raise Exception("Flowline not found: " + FLOWLINE_PATH)

if POINT_NAME:
    point_path = os.path.join(site_path, POINT_SUBDIR, POINT_NAME)
else:
    shps = sorted(glob.glob(os.path.join(site_path, POINT_SUBDIR, "*.shp")))
    shps = [s for s in shps if "_proj" not in os.path.basename(s)] or shps
    if not shps:
        raise Exception("No .shp in " + os.path.join(site_path, POINT_SUBDIR))
    point_path = shps[0]
print("Site point  :", point_path)

# --- target UTM (peek at first tile) ---------------------------------------
def utm_epsg_for(lon, lat):
    zone = int(math.floor((lon + 180.0) / 6.0) + 1)
    return (26900 + zone if lat >= 0 else 32700 + zone), zone

t0 = QgsRasterLayer(tiles[0], "t0")
if not t0.isValid():
    raise Exception("First tile invalid: " + tiles[0])
src_crs0 = t0.crs()
if TARGET_EPSG is not None:
    target_crs = QgsCoordinateReferenceSystem.fromEpsgId(int(TARGET_EPSG))
else:
    e = t0.extent()
    cx = (e.xMinimum() + e.xMaximum()) / 2.0
    cy = (e.yMinimum() + e.yMaximum()) / 2.0
    geo = QgsCoordinateReferenceSystem.fromEpsgId(4326)
    c = QgsCoordinateTransform(src_crs0, geo, project).transform(cx, cy)
    epsg, zone = utm_epsg_for(c.x(), c.y())
    target_crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
    print("Auto UTM zone %dN -> %s" % (zone, target_crs.authid()))

# =====================================================================
# 1-2. MERGE + REPROJECT THE 1 m DEM
# =====================================================================
print("\n[1/5] Merging tiles ...")
merged_raw = processing.run("gdal:merge", {
    "INPUT": tiles, "PCT": False, "SEPARATE": False,
    "NODATA_INPUT": None, "NODATA_OUTPUT": NODATA,
    "OPTIONS": "", "EXTRA": "", "DATA_TYPE": 5,        # Float32
    "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
m0 = QgsRasterLayer(merged_raw, "merged_raw")
if not m0.isValid():
    raise Exception("Merge produced invalid raster.")
print("    merged native CRS:", m0.crs().authid(),
      "| size:", m0.width(), "x", m0.height())

print("[2/5] Reprojecting DEM -> %s ..." % target_crs.authid())
if m0.crs().authid() == target_crs.authid():
    processing.run("gdal:translate", {
        "INPUT": merged_raw, "TARGET_CRS": target_crs, "NODATA": NODATA,
        "DATA_TYPE": 0, "OPTIONS": "", "EXTRA": "", "OUTPUT": MERGED_PATH})
else:
    processing.run("gdal:warpreproject", {
        "INPUT": merged_raw, "SOURCE_CRS": m0.crs(), "TARGET_CRS": target_crs,
        "RESAMPLING": RESAMPLING, "NODATA": NODATA, "TARGET_RESOLUTION": None,
        "OPTIONS": "", "DATA_TYPE": 0, "TARGET_EXTENT": None,
        "TARGET_EXTENT_CRS": None, "MULTITHREADING": False, "EXTRA": "",
        "OUTPUT": MERGED_PATH})
merged = QgsRasterLayer(MERGED_PATH, "demhr_merged_utm")
if not merged.isValid():
    raise Exception("Reprojected DEM invalid: " + MERGED_PATH)
print("    wrote", MERGED_PATH, "| size:", merged.width(), "x", merged.height())

# =====================================================================
# 3. TRACE THE SINGLE REACH ALONG THE NHD MAIN STEM
# =====================================================================
print("[3/5] Tracing reach: snap site point, walk main stem ...")

fl_utm = processing.run("native:reprojectlayer", {
    "INPUT": FLOWLINE_PATH, "TARGET_CRS": target_crs,
    "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
pt_utm = processing.run("native:reprojectlayer", {
    "INPUT": point_path, "TARGET_CRS": target_crs,
    "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]

# native: algorithms with a TEMPORARY_OUTPUT return a QgsVectorLayer object,
# not a path string -- use it directly if so, else open the path.
def as_layer(out, name):
    if isinstance(out, QgsVectorLayer):
        return out
    return QgsVectorLayer(out, name, "ogr")

fl_layer = as_layer(fl_utm, "fl")
pt_layer = as_layer(pt_utm, "pt")
if not fl_layer.isValid() or not pt_layer.isValid():
    raise Exception("Reprojected flowline/point invalid.")

site_feat = next(pt_layer.getFeatures(), None)
if site_feat is None:
    raise Exception("Site point layer is empty.")
site_pt = site_feat.geometry().asPoint()

# --- DIRECTED node graph from flowline segments ----------------------------
# Trust NHD's digitized direction. Each polyline is digitized from-node ->
# to-node IN THE DIRECTION OF FLOW, so segment vertex order is the flow
# direction. Two adjacencies:
#   down_adj[a] -> nodes reachable WITH the arrows (downstream)
#   up_adj[a]   -> nodes reachable AGAINST the arrows (upstream)
# We also tag every node that touches a MAIN-STEM segment (NAME_FIELD ==
# MAIN_STEM_NAME) so the site snaps onto the main stem, not the tributary.
# Snap coords to a small grid so shared endpoints between features become one
# node (NHD lines meet exactly, but floating noise can split them).
SNAP_TOL = 0.5  # m
def key(x, y):
    return (round(x / SNAP_TOL), round(y / SNAP_TOL))

down_adj = {}     # node_key -> list of (neighbor_key, seg_length, (nx, ny))  WITH flow
up_adj   = {}     # node_key -> list of (neighbor_key, seg_length, (nx, ny))  AGAINST flow
node_xy  = {}     # node_key -> (x, y)
mainstem_nodes = set()   # node_keys that lie on a main-stem segment
mainstem_edges = set()   # frozenset({ka,kb}) for edges on the main stem
def add_directed_edge(a, b, is_main):
    """a -> b is one flowline segment in the direction of flow (a upstream of b)."""
    (ax, ay), (bx, by) = a, b
    ka, kb = key(ax, ay), key(bx, by)
    if ka == kb:
        return
    node_xy[ka] = (ax, ay); node_xy[kb] = (bx, by)
    L = math.hypot(bx - ax, by - ay)
    down_adj.setdefault(ka, []).append((kb, L, (bx, by)))
    up_adj.setdefault(kb, []).append((ka, L, (ax, ay)))
    if is_main:
        mainstem_nodes.add(ka); mainstem_nodes.add(kb)
        mainstem_edges.add(frozenset((ka, kb)))

# does this layer actually have the name field?
fl_field_names = [fld.name() for fld in fl_layer.fields()]
have_name = NAME_FIELD in fl_field_names
if not have_name:
    print("    !! NAME_FIELD '%s' not in flowline; main-stem restriction disabled."
          % NAME_FIELD)

n_feat = n_main = 0
for f in fl_layer.getFeatures():
    g = f.geometry()
    if g.isEmpty():
        continue
    n_feat += 1
    is_main = bool(have_name and f[NAME_FIELD] == MAIN_STEM_NAME)
    if is_main:
        n_main += 1
    lines = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]
    for line in lines:
        for i in range(len(line) - 1):
            add_directed_edge((line[i].x(), line[i].y()),
                              (line[i + 1].x(), line[i + 1].y()), is_main)
print("    flowline features: %d (main-stem: %d) | graph nodes: %d | main nodes: %d"
      % (n_feat, n_main, len(node_xy), len(mainstem_nodes)))
if have_name and n_main == 0:
    raise Exception("No segments with %s == '%s'. Check the label you assigned."
                    % (NAME_FIELD, MAIN_STEM_NAME))

# --- snap site point to nearest MAIN-STEM node (within radius) --------------
# Restrict snap candidates to main-stem nodes so the site lands on the modeled
# channel, not the tributary it sits on. Fall back to all nodes if none labeled.
candidates = mainstem_nodes if mainstem_nodes else set(node_xy.keys())
best_k, best_d = None, None
for k in candidates:
    x, y = node_xy[k]
    d = math.hypot(x - site_pt.x(), y - site_pt.y())
    if best_d is None or d < best_d:
        best_k, best_d = k, d
if best_k is None:
    raise Exception("No flowline nodes to snap to.")
sx, sy = node_xy[best_k]
print("    snap distance to main stem: %.1f m" % best_d)
if mainstem_nodes and best_d > SNAP_RADIUS_M:
    print("    !! WARNING: nearest main-stem node is %.0f m away (> %.0f m radius)."
          % (best_d, SNAP_RADIUS_M))
    print("       The site may be farther from the labeled main stem than expected.")

# write snapped point
fldsP = QgsFields()
fldsP.append(QgsField("label", QVariant.String))
fldsP.append(QgsField("dist_m", QVariant.Double))
optP = QgsVectorFileWriter.SaveVectorOptions(); optP.driverName = "GPKG"
wP = QgsVectorFileWriter.create(SNAP_PATH, fldsP, QgsWkbTypes.Point,
                                target_crs, project.transformContext(), optP)
ftP = QgsFeature(fldsP)
ftP.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(sx, sy)))
ftP.setAttribute("label", "site_snapped"); ftP.setAttribute("dist_m", float(best_d))
wP.addFeature(ftP); del wP

# --- bounded DFS: longest onward path within a budget (main-stem chooser) ---
def longest_onward(graph, start, came_from, budget):
    best_len = 0.0
    for (nb, L, _b) in graph.get(start, []):
        if nb == came_from:
            continue
        if L >= budget:
            cand = budget
        else:
            cand = L + longest_onward(graph, nb, start, budget - L)
        if cand > best_len:
            best_len = cand
    return best_len

def trace(graph, budget, prefer_longest):
    """Walk from the snapped node up to `budget` metres along `graph` (up_adj for
       upstream, down_adj for downstream). At a fork the choice is, in order:
       (1) stay on a MAIN-STEM edge if one is available; (2) if prefer_longest,
       take the branch that continues furthest; (3) otherwise the first edge."""
    pts = [(sx, sy)]
    visited = {best_k}
    cur = best_k
    remaining = budget
    guard = 0
    while remaining > 1e-6 and guard < 200000:
        guard += 1
        nbrs = [(nb, L, b) for (nb, L, b) in graph.get(cur, []) if nb not in visited]
        if not nbrs:
            break
        # (1) prefer edges that are on the labeled main stem
        main_nbrs = [(nb, L, b) for (nb, L, b) in nbrs
                     if frozenset((cur, nb)) in mainstem_edges]
        pool = main_nbrs if main_nbrs else nbrs
        if (prefer_longest or main_nbrs) and len(pool) > 1:
            # among the preferred pool, take the furthest-continuing branch
            scored = []
            for (nb, L, b) in pool:
                onward = longest_onward(graph, nb, cur, max(remaining - L, 0.0))
                scored.append((L + onward, nb, L, b))
            scored.sort(key=lambda s: s[0], reverse=True)
            _, nb, L, b = scored[0]
        else:
            nb, L, b = pool[0]
        if L >= remaining:
            ax, ay = node_xy[cur]
            ux, uy = (b[0] - ax), (b[1] - ay)
            nrm = math.hypot(ux, uy) or 1.0
            pts.append((ax + ux / nrm * remaining, ay + uy / nrm * remaining))
            remaining = 0.0
            break
        pts.append(b)
        visited.add(nb)
        remaining -= L
        cur = nb
    return pts, (budget - remaining)

# Upstream = AGAINST the arrows (up_adj), main stem at forks.
# Downstream = WITH the arrows (down_adj), normally single-path.
up_pts,   up_walked   = trace(up_adj,   LEN_UPSTREAM_M,   prefer_longest=True)
down_pts, down_walked = trace(down_adj, LEN_DOWNSTREAM_M, prefer_longest=False)
print("    walked upstream  : %.0f m (target %.0f)" % (up_walked, LEN_UPSTREAM_M))
print("    walked downstream: %.0f m (target %.0f)" % (down_walked, LEN_DOWNSTREAM_M))

# --- direction sanity check against elevation (WARNING ONLY) ---------------
# Per the design decision, NHD's digitized direction is authoritative for
# up/down. We still cross-check against the DEM and WARN if they disagree (the
# upstream end should be higher), but we do NOT auto-swap -- if this fires,
# inspect and decide. (Reversed NHD lines are known in this watershed, which is
# why fillsink_etc.py auto-corrects them in the HMS pipeline.)
def sample_dem(x, y):
    val, ok = merged.dataProvider().sample(QgsPointXY(x, y), 1)
    return val if ok else None

up_end   = up_pts[-1]    if len(up_pts)   > 1 else (sx, sy)
down_end = down_pts[-1]  if len(down_pts) > 1 else (sx, sy)
z_up   = sample_dem(*up_end)
z_down = sample_dem(*down_end)
if z_up is not None and z_down is not None:
    print("    elevation check: upstream end %.1f m, downstream end %.1f m"
          % (z_up, z_down))
    if z_up < z_down - 1.0:   # 1 m tolerance against DEM noise
        print("    !! WARNING: upstream end is LOWER than downstream end.")
        print("       NHD direction may be reversed on the main stem here.")
        print("       Inspect reach_centerline; if wrong, swap LEN_UPSTREAM_M/")
        print("       LEN_DOWNSTREAM_M or fix the NHD line direction, then re-run.")

if up_walked < LEN_UPSTREAM_M - 1:
    print("    !! upstream trace short -- channel may end inside the watershed clip.")
if down_walked < LEN_DOWNSTREAM_M - 1:
    print("    !! downstream trace short -- reached the watershed outlet/DEM edge.")

# assemble: upstream end -> site -> downstream end
reach_pts = list(reversed(up_pts)) + down_pts[1:]
reach_geom = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in reach_pts])

fldsL = QgsFields(); fldsL.append(QgsField("len_m", QVariant.Double))
optL = QgsVectorFileWriter.SaveVectorOptions(); optL.driverName = "GPKG"
wL = QgsVectorFileWriter.create(CENTERLINE_PATH, fldsL, QgsWkbTypes.LineString,
                                target_crs, project.transformContext(), optL)
fL = QgsFeature(fldsL); fL.setGeometry(reach_geom)
fL.setAttribute("len_m", float(reach_geom.length()))
wL.addFeature(fL); del wL
print("    reach length: %.0f m -> %s" % (reach_geom.length(), CENTERLINE_PATH))

# =====================================================================
# 4. BUFFER -> CORRIDOR
# =====================================================================
print("[4/5] Buffering reach by %.0f m ..." % HALF_WIDTH_M)
processing.run("native:buffer", {
    "INPUT": CENTERLINE_PATH, "DISTANCE": HALF_WIDTH_M,
    "SEGMENTS": 8, "END_CAP_STYLE": 0, "JOIN_STYLE": 0,
    "MITER_LIMIT": 2, "DISSOLVE": True, "OUTPUT": CORRIDOR_PATH})
corridor = QgsVectorLayer(CORRIDOR_PATH, "reach_corridor", "ogr")
if not corridor.isValid() or corridor.featureCount() == 0:
    raise Exception("Corridor buffer empty/invalid.")

# =====================================================================
# 5. CLIP DEM TO CORRIDOR BOUNDING BOX
# =====================================================================
ext = corridor.extent()
extent_str = ("%f,%f,%f,%f [%s]" % (
    ext.xMinimum(), ext.xMaximum(), ext.yMinimum(), ext.yMaximum(),
    target_crs.authid()))
print("[5/5] Clipping DEM to corridor bbox:", extent_str)
processing.run("gdal:cliprasterbyextent", {
    "INPUT": MERGED_PATH, "PROJWIN": extent_str,
    "OVERCRS": False, "NODATA": NODATA, "OPTIONS": "", "DATA_TYPE": 0,
    "EXTRA": "", "OUTPUT": TERRAIN_PATH})
terrain = QgsRasterLayer(TERRAIN_PATH, "ras_terrain")
if not terrain.isValid():
    raise Exception("RAS terrain invalid: " + TERRAIN_PATH)
st = terrain.dataProvider().bandStatistics(1)
print("    wrote", TERRAIN_PATH)
print("    terrain CRS:", terrain.crs().authid(),
      "| size:", terrain.width(), "x", terrain.height())
print("    elevation range: %.2f .. %.2f m" % (st.minimumValue, st.maximumValue))

if ADD_TO_PROJECT:
    for p, nm in [(TERRAIN_PATH, "ras_terrain"),
                  (CORRIDOR_PATH, "reach_corridor"),
                  (CENTERLINE_PATH, "reach_centerline"),
                  (SNAP_PATH, "site_on_channel")]:
        lyr = QgsVectorLayer(p, nm, "ogr") if p.endswith(".gpkg") else QgsRasterLayer(p, nm)
        if lyr.isValid():
            project.addMapLayer(lyr)

print("\n" + "=" * 70)
print("STEP 1 COMPLETE.")
print("=" * 70)
print("RAS terrain :", TERRAIN_PATH)
print("Centerline  :", CENTERLINE_PATH)
print("Snapped pt  :", SNAP_PATH, "(check snap distance above)")
print("\nVERIFY before step 2:")
print("  - site_on_channel snapped to the intended channel (not a tributary)")
print("  - reach_centerline runs main-stem, ~%.0f m total"
      % (LEN_UPSTREAM_M + LEN_DOWNSTREAM_M))
print("  - ras_terrain elevation range is sensible for the site")
print("\nNEXT: step 2 -- centerline, banks, flow paths, cross-section cut lines.")
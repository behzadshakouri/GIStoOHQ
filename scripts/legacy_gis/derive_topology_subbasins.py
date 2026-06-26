# =============================================================================
# derive_topology_subbasins.py   (QGIS Python Console)
#
# PHASE 2 half of derive_topology.py: wire each SUBBASIN to its downstream
# element, AFTER pour points exist, subwatershed_params.gpkg has been built, and
# junctions.gpkg has been materialized (materialize_junctions.py).
#
# MODEL
#   At this site each subbasin's pour point sits at (or very near) a JUNCTION
#   -- the confluence where its flow joins the network. So a subbasin drains to
#   its NEAREST JUNCTION. A pour point with no junction within SNAP_TOL is a
#   deliberate mid-reach split; it falls back to the nearest reach UPSTREAM end
#   and is flagged 'reach' so it surfaces in the debug column.
#
#   subbasin id == pour point id (coerced to int on both sides).
#
# OUTPUTS (in <SITE>/outputs/)
#   subwatershed_params.gpkg            + ds_kind ('junction'|'reach'),
#                                         ds_junction_id, ds_reach_id,
#                                         ds_dist_m, ds_debug (human-readable)
#   topology_connectors_subbasins.gpkg  subbasin CENTROID -> junction (or reach
#                                         up end) verification lines, carrying
#                                         the same ds_debug text for labeling
#
# VERIFY: render topology_connectors_subbasins over reaches + junctions +
# pour points; each connector runs from a subwatershed centroid to the junction
# its pour point sits on. Label the layer by 'ds_debug' to read the wiring.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFields, QgsFeature,
    QgsGeometry, QgsPointXY, QgsVectorFileWriter, QgsWkbTypes,
    QgsCoordinateTransformContext
)
from qgis.PyQt.QtCore import QVariant

# --- root resolution -------------------------------------------------------
# Set ONCE per session in the console, BEFORE running any script:
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"      # Arash
#   ROOT = "C:/Users/smnfa/Dropbox/NHA/"           # Samaneh
#   SITE_DIR = "WS3_GIS/AZ12-100"
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

REACHES_NAME  = "reaches.gpkg"
JUNC_NAME     = "junctions.gpkg"
JUNC_LAYER    = "junctions"
JUNC_ID_FIELD = "junction_id"
PARAMS_NAME   = "subwatershed_params.gpkg"
PARAMS_LAYER  = "subwatershed_params"
POURPTS_NAME  = "pour_points_snapped.gpkg"

PIXEL_M = 9.336
SNAP_PX = 1.5
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
junc_p    = os.path.join(OUT_DIR, JUNC_NAME)
params_p  = os.path.join(OUT_DIR, PARAMS_NAME)
pourpts_p = os.path.join(OUT_DIR, POURPTS_NAME)
connect_p = os.path.join(OUT_DIR, "topology_connectors_subbasins.gpkg")
SNAP_TOL  = SNAP_PX * PIXEL_M

for p in (reaches_p, junc_p, params_p, pourpts_p):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

print("Reaches    :", reaches_p)
print("Junctions  :", junc_p)
print("Params     :", params_p)
print("Pour points:", pourpts_p)
print("Snap tol   : %.2f m (%.1f px)" % (SNAP_TOL, SNAP_PX))

def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# load reaches; build oriented endpoints (must match phase-1 orientation)
# (reach up ends are the fallback target when no junction is near a pour point)
# ---------------------------------------------------------------------------
reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer")

def line_endpoints(geom):
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        pts = [p for part in parts for p in part]
    else:
        pts = geom.asPolyline()
    return QgsPointXY(pts[0]), QgsPointXY(pts[-1])

rinfo = {}
for f in reaches.getFeatures():
    fp, lp = line_endpoints(f.geometry())
    zu, zd = f["z_up_m"], f["z_dn_m"]
    if zu is not None and zd is not None and zd > zu:
        up_pt, dn_pt = lp, fp
    else:
        up_pt, dn_pt = fp, lp
    rinfo[f.id()] = {"up": up_pt, "dn": dn_pt}

print("\nReaches loaded :", len(rinfo))

# ---------------------------------------------------------------------------
# load junctions: junction_id -> point
# ---------------------------------------------------------------------------
juncs = QgsVectorLayer(junc_p + "|layername=" + JUNC_LAYER, "junctions", "ogr")
if not juncs.isValid():
    juncs = QgsVectorLayer(junc_p, "junctions", "ogr")
if not juncs.isValid():
    raise Exception("invalid junctions layer")

jfields = [f.name() for f in juncs.fields()]
if JUNC_ID_FIELD not in jfields:
    raise Exception("junctions.gpkg has no '%s' field. Found: %s"
                    % (JUNC_ID_FIELD, jfields))

jinfo = {}   # junction_id (int) -> QgsPointXY
for f in juncs.getFeatures():
    jid = as_int(f[JUNC_ID_FIELD])
    g = f.geometry()
    if jid is None or g is None or g.isEmpty():
        continue
    jinfo[jid] = QgsPointXY(g.asPoint())

print("Junctions loaded:", len(jinfo))
if not jinfo:
    raise Exception("no junctions found -- run materialize_junctions.py first")

# ---------------------------------------------------------------------------
# load subbasin centroids (origin of connector lines) by id
# ---------------------------------------------------------------------------
subs = QgsVectorLayer(params_p + "|layername=" + PARAMS_LAYER, "subs", "ogr")
if not subs.isValid():
    raise Exception("invalid subbasin layer")

cent_xy = {}    # subbasin id (int) -> (x,y) centroid
for f in subs.getFeatures():
    sid = as_int(f["id"])
    cx, cy = f["centroid_x"], f["centroid_y"]
    if sid is not None and cx is not None and cy is not None:
        try:
            cent_xy[sid] = (float(cx), float(cy))
        except (TypeError, ValueError):
            pass

# ---------------------------------------------------------------------------
# subbasin -> nearest JUNCTION (fallback: nearest reach UP end)
# subbasin id == pour point id
# ---------------------------------------------------------------------------
pts = QgsVectorLayer(pourpts_p, "pour", "ogr")
if not pts.isValid():
    raise Exception("invalid pour-point layer")

sub_kind  = {}   # id -> 'junction' | 'reach'
sub_junc  = {}   # id -> junction_id   (or None)
sub_reach = {}   # id -> reach fid     (or None)
sub_dist  = {}   # id -> distance to chosen target (m)
sub_dbg   = {}   # id -> human-readable wiring string
pp_present = set()

for pt in pts.getFeatures():
    pid = as_int(pt["id"])
    if pid is None:
        continue
    pp_present.add(pid)
    g = pt.geometry()
    p = QgsPointXY(g.asPoint())

    # nearest junction
    bj, bjd = None, None
    for jid, jp in jinfo.items():
        d = p.distance(jp)
        if bjd is None or d < bjd:
            bj, bjd = jid, d

    if bjd is not None and bjd <= SNAP_TOL:
        sub_kind[pid]  = "junction"
        sub_junc[pid]  = bj
        sub_reach[pid] = None
        sub_dist[pid]  = bjd
        sub_dbg[pid]   = "sub %d -> junction %d (%.1f m)" % (pid, bj, bjd)
    else:
        # fallback: nearest reach UPSTREAM end
        br, brd = None, None
        for fid, e in rinfo.items():
            d = p.distance(e["up"])
            if brd is None or d < brd:
                br, brd = fid, d
        sub_kind[pid]  = "reach"
        sub_junc[pid]  = None
        sub_reach[pid] = br
        sub_dist[pid]  = brd
        sub_dbg[pid]   = ("sub %d -> reach %s up-end (%.1f m) "
                          "[NO junction within %.1f m]"
                          % (pid, br, brd, SNAP_TOL))

n_junc  = sum(1 for k in sub_kind.values() if k == "junction")
n_reach = sum(1 for k in sub_kind.values() if k == "reach")
print("\nSubbasins wired: %d to junctions, %d to reach up-ends (fallback)."
      % (n_junc, n_reach))
if n_reach:
    print("  NOTE: %d pour point(s) had no junction within %.1f m. They were"
          " wired to the nearest reach up-end and flagged 'reach' in ds_debug."
          % (n_reach, SNAP_TOL))

# ---------------------------------------------------------------------------
# write ds_kind / ds_junction_id / ds_reach_id / ds_dist_m / ds_debug
# onto subwatershed_params.gpkg
# ---------------------------------------------------------------------------
subs.startEditing()
have = [f.name() for f in subs.fields()]
want = [("ds_kind",        QVariant.String),
        ("ds_junction_id", QVariant.Int),
        ("ds_reach_id",    QVariant.Int),
        ("ds_dist_m",      QVariant.Double),
        ("ds_debug",       QVariant.String)]
add = [QgsField(n, t) for (n, t) in want if n not in have]
if add:
    subs.dataProvider().addAttributes(add); subs.updateFields()
i_kind = subs.fields().indexFromName("ds_kind")
i_jid  = subs.fields().indexFromName("ds_junction_id")
i_rid  = subs.fields().indexFromName("ds_reach_id")
i_dst  = subs.fields().indexFromName("ds_dist_m")
i_dbg  = subs.fields().indexFromName("ds_debug")
for f in subs.getFeatures():
    sid = as_int(f["id"])
    if sid not in sub_kind:
        subs.changeAttributeValue(f.id(), i_dbg,
                                  "sub %s -> NO pour point matched" % str(sid))
        continue
    jid = sub_junc.get(sid); rid = sub_reach.get(sid)
    subs.changeAttributeValue(f.id(), i_kind, sub_kind[sid])
    subs.changeAttributeValue(f.id(), i_jid, None if jid is None else int(jid))
    subs.changeAttributeValue(f.id(), i_rid, None if rid is None else int(rid))
    subs.changeAttributeValue(f.id(), i_dst, float(sub_dist[sid]))
    subs.changeAttributeValue(f.id(), i_dbg, sub_dbg[sid])
subs.commitChanges()
print("Wrote ds_kind / ds_junction_id / ds_reach_id / ds_dist_m / ds_debug"
      " to subwatershed_params.gpkg")

# ---------------------------------------------------------------------------
# verification connectors: subbasin CENTROID -> junction (or reach up end)
# ---------------------------------------------------------------------------
flds = QgsFields()
flds.append(QgsField("kind",     QVariant.String))   # 'junction' | 'reach'
flds.append(QgsField("sub_id",   QVariant.Int))
flds.append(QgsField("dst_junc", QVariant.Int))
flds.append(QgsField("dst_reach",QVariant.Int))
flds.append(QgsField("dist_m",   QVariant.Double))
flds.append(QgsField("ds_debug", QVariant.String))

# release any project lock on this GPKG (Windows) before overwriting
_proj = QgsProject.instance()
for _lyr in list(_proj.mapLayers().values()):
    try:
        _src = _lyr.source().split("|", 1)[0]
    except Exception:
        _src = ""
    if os.path.normcase(os.path.abspath(_src)) == \
       os.path.normcase(os.path.abspath(connect_p)):
        _proj.removeMapLayer(_lyr.id())

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"; opts.layerName = "topology_connectors_subbasins"
opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
writer = QgsVectorFileWriter.create(
    connect_p, flds, QgsWkbTypes.LineString, reaches.crs(),
    QgsCoordinateTransformContext(), opts)

n_written = 0
n_nocent  = 0
for sid in sub_kind:
    if sid not in cent_xy:
        n_nocent += 1
        continue
    ax, ay = cent_xy[sid]
    a = QgsPointXY(ax, ay)
    if sub_kind[sid] == "junction":
        jid = sub_junc[sid]
        b = jinfo[jid]
    else:
        rid = sub_reach[sid]
        b = rinfo[rid]["up"]
    if a.distance(b) < 0.01:        # skip degenerate (zero-length) connectors
        continue
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([a, b]))
    ft["kind"]      = sub_kind[sid]
    ft["sub_id"]    = int(sid)
    ft["dst_junc"]  = None if sub_junc[sid]  is None else int(sub_junc[sid])
    ft["dst_reach"] = None if sub_reach[sid] is None else int(sub_reach[sid])
    ft["dist_m"]    = float(sub_dist[sid])
    ft["ds_debug"]  = sub_dbg[sid]
    writer.addFeature(ft)
    n_written += 1
del writer
print("Wrote %d connector(s) -> %s" % (n_written, os.path.basename(connect_p)))
if n_nocent:
    print("  WARNING: %d subbasin(s) had no centroid_x/y -- no connector drawn"
          " (centroid comes from extract_slope.py)." % n_nocent)

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
print("\nSUBBASIN TOPOLOGY SUMMARY")
print("  subbasin_id -> downstream element")
for sid in sorted(sub_dbg):
    print("    " + sub_dbg[sid])

# any subbasin present in params but with no matching pour point?
missing_pp = [as_int(f["id"]) for f in subs.getFeatures()
              if as_int(f["id"]) is not None and as_int(f["id"]) not in pp_present]
if missing_pp:
    print("\n  WARNING: subbasin id(s) with no matching pour point:",
          sorted(missing_pp))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(connect_p, "topology_connectors_subbasins", "ogr"))
    print("\n  loaded topology_connectors_subbasins")

print("\nDone (phase 2 subbasin topology).")
print("VERIFY: label topology_connectors_subbasins by 'ds_debug'; each line")
print("should run from a subwatershed centroid to the junction its pour point")
print("sits on. Any line flagged '[NO junction ...]' is a mid-reach fallback.")

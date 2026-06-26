# =============================================================================
# prune_internal_reaches.py   (QGIS Python Console)
#
# NHA WS3 -- PHASE 1 cleanup: finalize the reach + junction network BEFORE pour
# points are placed. Run AFTER materialize_junctions.py, BEFORE phase 2.
#
# WHY
#   r.stream.extract leaves short channel segments that are INTERNAL to a single
#   subbasin: a reach whose upstream end has no upstream reach feeding it has no
#   independent contributing area in a lumped model -- its drainage is already
#   represented by the subbasin whose pour point sits at the junction/outlet at
#   its downstream end. Routing flow through such a reach (then through the
#   subbasin transform) double-counts. These reaches must be dropped, and any
#   junction that is left with < 2 surviving inflows is no longer a confluence
#   and must be collapsed (bypassed).
#
# ALGORITHM
#   1. Orient every reach by z (z_dn < z_up), giving up/dn endpoints.
#   2. UPSTREAM-FEEDER TEST. A reach R is FED if any OTHER reach's dn end
#      coincides (within SNAP_TOL) with R's up end. (Subbasins do not exist yet
#      in phase 1, so "fed" here means: another reach flows into R's top.)
#      A reach with NO upstream feeder is INTERNAL -> drop it.
#      NOTE: the outlet reach is never dropped.
#   3. Iterate the prune to a fixed point: dropping a reach can orphan the reach
#      below it (its former feeder is gone), so repeat until no reach is dropped.
#   4. COLLAPSE JUNCTIONS by surviving inflow count. For each junction, count
#      surviving reaches whose dn end is at it (in_count). Then:
#         in_count >= 2  -> real confluence, KEEP.
#         in_count == 1  -> pass-through: the single inflow reach is re-pointed
#                           to the junction's downstream element; junction DROP.
#         in_count == 0  -> orphan: junction DROP. Anything draining to it is
#                           re-pointed to its downstream element.
#   5. Re-point: every reach that pointed at a dropped junction now points at
#      that junction's downstream element (chasing through chains of collapsed
#      junctions to the first surviving element or the outlet/Sink).
#   6. Rewrite reaches.gpkg (surviving reaches, updated ds_type/ds_reach_id/
#      ds_junction_id) and junctions.gpkg (surviving junctions only).
#
# After this runs, phase-2 derive_topology_subbasins.py wires subbasins against
# ONLY the surviving reaches + junctions, so a pour point at a former internal
# reach's outlet now resolves to the junction there (no tolerance change needed).
#
# OUTPUTS (rewritten in place, in <SITE>/outputs/)
#   reaches.gpkg     pruned to non-internal reaches; ds_* updated
#   junctions.gpkg   pruned to real confluences (>=2 surviving inflows)
#   pruned_reaches.gpkg / pruned_junctions.gpkg   the dropped elements, for audit
#
# VERIFY: render reaches + junctions; every junction has >=2 reaches in and 1
# out; no dangling internal stubs remain; exactly one element reaches the outlet.
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
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

REACHES_NAME = "reaches.gpkg"
JUNC_NAME    = "junctions.gpkg"
JUNC_LAYER   = "junctions"

PIXEL_M = 9.336
SNAP_PX = 1.5
RELOAD_IN_PROJECT = True
WRITE_AUDIT = True            # write pruned_reaches/junctions for inspection
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
junc_p    = os.path.join(OUT_DIR, JUNC_NAME)
SNAP_TOL  = SNAP_PX * PIXEL_M

for p in (reaches_p, junc_p):
    if not os.path.isfile(p):
        raise Exception("not found: " + p + " (run phase-1 up to "
                        "materialize_junctions.py first)")

print("Reaches  :", reaches_p)
print("Junctions:", junc_p)
print("Snap tol : %.2f m (%.1f px)" % (SNAP_TOL, SNAP_PX))


def iid(v):
    if v is None:
        return None
    try:
        if hasattr(v, "isNull") and v.isNull():
            return None
    except Exception:
        pass
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def line_endpoints(geom):
    if geom.isMultipart():
        pts = [p for part in geom.asMultiPolyline() for p in part]
    else:
        pts = geom.asPolyline()
    return QgsPointXY(pts[0]), QgsPointXY(pts[-1])


# ---------------------------------------------------------------------------
# load reaches into a plain dict model we can mutate, keyed by reach_id
# ---------------------------------------------------------------------------
reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer")
rfields = [f.name() for f in reaches.fields()]
for need in ("reach_id", "ds_type", "ds_reach_id"):
    if need not in rfields:
        raise Exception("reaches.gpkg missing '%s' -- run derive_topology_"
                        "reaches.py + materialize_junctions.py first" % need)
has_dsj = "ds_junction_id" in rfields

R = {}     # reach_id -> dict(up, dn, ds_type, ds_reach_id, ds_junction_id, fid, geom_wkb)
for f in reaches.getFeatures():
    rid = iid(f["reach_id"])
    if rid is None:
        rid = int(f.id())
    fp, lp = line_endpoints(f.geometry())
    zu, zd = f["z_up_m"], f["z_dn_m"]
    try:
        zu = float(zu); zd = float(zd)
        swap = zd > zu
    except (TypeError, ValueError):
        swap = False
    up_pt, dn_pt = (lp, fp) if swap else (fp, lp)
    R[rid] = {
        "up": up_pt, "dn": dn_pt,
        "ds_type": f["ds_type"],
        "ds_reach_id": iid(f["ds_reach_id"]),
        "ds_junction_id": iid(f["ds_junction_id"]) if has_dsj else None,
        "fid": f.id(),
        "geom": QgsGeometry(f.geometry()),
    }

print("\nReaches loaded:", len(R))

# ---------------------------------------------------------------------------
# load junctions: junction_id -> dict(pt, ds_type, ds_reach_id)
# ---------------------------------------------------------------------------
juncs = QgsVectorLayer(junc_p + "|layername=" + JUNC_LAYER, "junctions", "ogr")
if not juncs.isValid():
    juncs = QgsVectorLayer(junc_p, "junctions", "ogr")
if not juncs.isValid():
    raise Exception("invalid junctions layer")

J = {}     # junction_id -> dict(pt, ds_type, ds_reach_id)
for f in juncs.getFeatures():
    jid = iid(f["junction_id"])
    if jid is None:
        continue
    g = f.geometry()
    J[jid] = {
        "pt": QgsPointXY(g.asPoint()),
        "ds_type": f["ds_type"],
        "ds_reach_id": iid(f["ds_reach_id"]),
    }
print("Junctions loaded:", len(J))

# identify the outlet reach (never pruned)
outlet_reaches = [rid for rid, r in R.items() if r["ds_type"] == "outlet"]
print("Outlet reach id(s):", outlet_reaches)

# ---------------------------------------------------------------------------
# STEP 2-3: iterative upstream-feeder prune
#   A reach is FED if some other (surviving) reach's dn end is within SNAP_TOL
#   of its up end. Unfed, non-outlet reaches are internal -> drop. Repeat until
#   no change (dropping a reach can unfeed the one below it).
# ---------------------------------------------------------------------------
alive = set(R.keys())
dropped_reaches = []

def is_fed(rid, alive_set):
    up = R[rid]["up"]
    for o in alive_set:
        if o == rid:
            continue
        if R[o]["dn"].distance(up) <= SNAP_TOL:
            return True
    return False

while True:
    to_drop = [rid for rid in alive
               if rid not in outlet_reaches and not is_fed(rid, alive)]
    if not to_drop:
        break
    for rid in to_drop:
        alive.discard(rid)
        dropped_reaches.append(rid)

print("\nInternal reaches dropped:", sorted(dropped_reaches)
      if dropped_reaches else "none")
print("Surviving reaches:", len(alive))

# ---------------------------------------------------------------------------
# STEP 4: count surviving inflows per junction (reaches whose dn end is at it)
# ---------------------------------------------------------------------------
def reaches_into_junction(jid, alive_set):
    jp = J[jid]["pt"]
    return [rid for rid in alive_set if R[rid]["dn"].distance(jp) <= SNAP_TOL]

junc_in = {jid: reaches_into_junction(jid, alive) for jid in J}

# classify junctions
keep_junc = set()
drop_junc = set()
for jid, ins in junc_in.items():
    if len(ins) >= 2:
        keep_junc.add(jid)
    else:
        drop_junc.add(jid)

print("\nJunctions kept (>=2 inflows):", sorted(keep_junc) if keep_junc else "none")
print("Junctions collapsed (<2 inflows):",
      sorted(drop_junc) if drop_junc else "none")

# ---------------------------------------------------------------------------
# STEP 5: resolve downstream targets, chasing through dropped junctions.
#   Each reach currently points at a junction (ds_junction_id) or reach
#   (ds_reach_id) or is the outlet. We resolve to a SURVIVING target:
#     - if it points at a kept junction      -> ('junction', jid)
#     - if it points at a dropped junction    -> follow that junction's
#       downstream element, recursively, until a kept junction / surviving
#       reach / outlet is reached.
#     - if it points at a reach that survived  -> ('reach', rid)
#     - if it points at a reach that was dropped (shouldn't happen for a kept
#       reach, but guard) -> chase that reach's own downstream.
# ---------------------------------------------------------------------------
def resolve_junction_target(jid, seen=None):
    """Return ('junction', jid) if kept, else follow its ds element."""
    if seen is None:
        seen = set()
    if jid in seen:
        return ("outlet", None)        # cycle guard
    seen.add(jid)
    if jid in keep_junc:
        return ("junction", jid)
    # dropped junction: follow its downstream
    jd = J[jid]
    if jd["ds_type"] == "outlet" or jd["ds_reach_id"] is None:
        return ("outlet", None)
    return resolve_reach_target(jd["ds_reach_id"], seen)

def resolve_reach_target(rid, seen=None):
    """Resolve a pointer AT reach rid into a surviving target."""
    if seen is None:
        seen = set()
    key = ("R", rid)
    if key in seen:
        return ("outlet", None)
    seen.add(key)
    if rid in alive:
        return ("reach", rid)
    # reach was dropped: follow ITS downstream pointer
    r = R[rid]
    if r["ds_type"] == "outlet":
        return ("outlet", None)
    if r["ds_type"] == "junction" and r["ds_junction_id"] is not None:
        return resolve_junction_target(r["ds_junction_id"], seen)
    if r["ds_reach_id"] is not None:
        return resolve_reach_target(r["ds_reach_id"], seen)
    return ("outlet", None)

# recompute each surviving reach's downstream against the pruned network
for rid in alive:
    r = R[rid]
    if rid in outlet_reaches:
        r["ds_type"] = "outlet"; r["ds_reach_id"] = None; r["ds_junction_id"] = None
        continue
    # a surviving reach's dn end sits at a junction (built by materialize_
    # junctions) -- find which junction, then resolve it through collapses.
    at_junc = [jid for jid in J if R[rid]["dn"].distance(J[jid]["pt"]) <= SNAP_TOL]
    if at_junc:
        kind, tgt = resolve_junction_target(at_junc[0])
    elif r["ds_type"] == "reach" and r["ds_reach_id"] is not None:
        kind, tgt = resolve_reach_target(r["ds_reach_id"])
    else:
        kind, tgt = ("outlet", None)
    if kind == "junction":
        r["ds_type"] = "junction"; r["ds_junction_id"] = tgt; r["ds_reach_id"] = None
    elif kind == "reach":
        r["ds_type"] = "reach"; r["ds_reach_id"] = tgt; r["ds_junction_id"] = None
    else:
        r["ds_type"] = "outlet"; r["ds_reach_id"] = None; r["ds_junction_id"] = None

# resolve each KEPT junction's downstream the same way (it may have pointed at a
# reach that was dropped, or at another junction that collapsed)
for jid in keep_junc:
    jd = J[jid]
    if jd["ds_type"] == "outlet" or jd["ds_reach_id"] is None:
        jd["_ds_kind"], jd["_ds_tgt"] = "outlet", None
        continue
    kind, tgt = resolve_reach_target(jd["ds_reach_id"])
    # a junction can only drain to a reach or the outlet in HMS
    if kind == "reach":
        jd["_ds_kind"], jd["_ds_tgt"] = "reach", tgt
    else:
        jd["_ds_kind"], jd["_ds_tgt"] = "outlet", None

# ---------------------------------------------------------------------------
# STEP 6: rewrite reaches.gpkg (surviving only) and junctions.gpkg (kept only)
# ---------------------------------------------------------------------------
def release_lock(path):
    proj = QgsProject.instance()
    for lyr in list(proj.mapLayers().values()):
        try:
            src = lyr.source().split("|", 1)[0]
        except Exception:
            src = ""
        if os.path.normcase(os.path.abspath(src)) == \
           os.path.normcase(os.path.abspath(path)):
            proj.removeMapLayer(lyr.id())

# -- reaches --
rflds = QgsFields()
rflds.append(QgsField("reach_id", QVariant.Int))
rflds.append(QgsField("ds_type", QVariant.String))
rflds.append(QgsField("ds_reach_id", QVariant.Int))
rflds.append(QgsField("ds_junction_id", QVariant.Int))
rflds.append(QgsField("z_up_m", QVariant.Double))
rflds.append(QgsField("z_dn_m", QVariant.Double))
# carry through any HMS routing params that exist on the source
carry = [n for n in ("length_m", "slope_mm", "base_w_m", "side_z",
                     "manning_n", "route_method") if n in rfields]
src_type = {f.name(): f.type() for f in reaches.fields()}
for n in carry:
    rflds.append(QgsField(n, src_type[n]))

# index source features by reach_id for attribute carry-through
src_feat = {}
for f in reaches.getFeatures():
    rid = iid(f["reach_id"])
    if rid is None:
        rid = int(f.id())
    src_feat[rid] = f

release_lock(reaches_p)
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"; opts.layerName = "reaches"
opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
w = QgsVectorFileWriter.create(reaches_p, rflds, QgsWkbTypes.LineString,
                               reaches.crs(), QgsCoordinateTransformContext(), opts)
for rid in sorted(alive):
    r = R[rid]; sf = src_feat[rid]
    ft = QgsFeature(rflds)
    ft.setGeometry(r["geom"])
    ft["reach_id"] = int(rid)
    ft["ds_type"] = r["ds_type"]
    ft["ds_reach_id"] = None if r["ds_reach_id"] is None else int(r["ds_reach_id"])
    ft["ds_junction_id"] = None if r["ds_junction_id"] is None else int(r["ds_junction_id"])
    ft["z_up_m"] = sf["z_up_m"]; ft["z_dn_m"] = sf["z_dn_m"]
    for n in carry:
        ft[n] = sf[n]
    w.addFeature(ft)
del w
print("\nRewrote reaches.gpkg ->", len(alive), "surviving reach(es)")

# -- junctions --
jflds = QgsFields()
jflds.append(QgsField("junction_id", QVariant.Int))
jflds.append(QgsField("x", QVariant.Double))
jflds.append(QgsField("y", QVariant.Double))
jflds.append(QgsField("ds_type", QVariant.String))
jflds.append(QgsField("ds_reach_id", QVariant.Int))
jflds.append(QgsField("n_in", QVariant.Int))

release_lock(junc_p)
opts2 = QgsVectorFileWriter.SaveVectorOptions()
opts2.driverName = "GPKG"; opts2.layerName = JUNC_LAYER
opts2.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
wj = QgsVectorFileWriter.create(junc_p, jflds, QgsWkbTypes.Point,
                                reaches.crs(), QgsCoordinateTransformContext(), opts2)
for jid in sorted(keep_junc):
    jd = J[jid]; pt = jd["pt"]
    ft = QgsFeature(jflds)
    ft.setGeometry(QgsGeometry.fromPointXY(pt))
    ft["junction_id"] = int(jid)
    ft["x"] = pt.x(); ft["y"] = pt.y()
    ft["ds_type"] = "outlet" if jd["_ds_kind"] == "outlet" else "reach"
    ft["ds_reach_id"] = None if jd["_ds_tgt"] is None else int(jd["_ds_tgt"])
    ft["n_in"] = len(junc_in[jid])
    wj.addFeature(ft)
del wj
print("Rewrote junctions.gpkg ->", len(keep_junc), "kept junction(s)")

# -- audit layers --
if WRITE_AUDIT and (dropped_reaches or drop_junc):
    aud_r = os.path.join(OUT_DIR, "pruned_reaches.gpkg")
    aud_j = os.path.join(OUT_DIR, "pruned_junctions.gpkg")
    if dropped_reaches:
        af = QgsFields(); af.append(QgsField("reach_id", QVariant.Int))
        release_lock(aud_r)
        o = QgsVectorFileWriter.SaveVectorOptions()
        o.driverName = "GPKG"; o.layerName = "pruned_reaches"
        o.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        ww = QgsVectorFileWriter.create(aud_r, af, QgsWkbTypes.LineString,
                                        reaches.crs(), QgsCoordinateTransformContext(), o)
        for rid in dropped_reaches:
            ft = QgsFeature(af); ft.setGeometry(R[rid]["geom"]); ft["reach_id"] = int(rid)
            ww.addFeature(ft)
        del ww
    if drop_junc:
        af = QgsFields(); af.append(QgsField("junction_id", QVariant.Int))
        release_lock(aud_j)
        o = QgsVectorFileWriter.SaveVectorOptions()
        o.driverName = "GPKG"; o.layerName = "pruned_junctions"
        o.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        ww = QgsVectorFileWriter.create(aud_j, af, QgsWkbTypes.Point,
                                        reaches.crs(), QgsCoordinateTransformContext(), o)
        for jid in drop_junc:
            ft = QgsFeature(af); ft.setGeometry(QgsGeometry.fromPointXY(J[jid]["pt"]))
            ft["junction_id"] = int(jid); ww.addFeature(ft)
        del ww
    print("Wrote audit layers: pruned_reaches.gpkg / pruned_junctions.gpkg")

# ---------------------------------------------------------------------------
# report + reload
# ---------------------------------------------------------------------------
print("\nFINAL NETWORK SUMMARY")
print("  reach_id -> downstream")
for rid in sorted(alive):
    r = R[rid]
    if r["ds_type"] == "junction":
        tgt = "Junction_%s" % r["ds_junction_id"]
    elif r["ds_type"] == "reach":
        tgt = "Reach_%s" % r["ds_reach_id"]
    else:
        tgt = "OUTLET"
    print("    Reach_%-4s -> %s" % (rid, tgt))
print("  junction_id -> downstream  (inflows)")
for jid in sorted(keep_junc):
    jd = J[jid]
    tgt = "OUTLET" if jd["_ds_kind"] == "outlet" else "Reach_%s" % jd["_ds_tgt"]
    print("    Junction_%-3s -> %s  (%d in)" % (jid, tgt, len(junc_in[jid])))

if RELOAD_IN_PROJECT:
    release_lock(reaches_p); release_lock(junc_p)
    QgsProject.instance().addMapLayer(QgsVectorLayer(reaches_p, "reaches", "ogr"))
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(junc_p + "|layername=" + JUNC_LAYER, "junctions", "ogr"))
    print("\n  reloaded reaches + junctions")

print("\nDone (phase 1 prune). Every surviving junction has >=2 inflows and 1")
print("out; internal stubs removed. Place pour points, then run run_phase2.py.")
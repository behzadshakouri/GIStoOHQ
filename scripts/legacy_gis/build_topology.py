# =============================================================================
# build_topology.py   (QGIS Python Console)
#
# NHA WS3 -- single source of truth for HMS network connectivity. Runs in
# PHASE 2 after compute_tc.py (so reaches, junctions, pour points, and
# subwatershed params all exist) and BEFORE write_basin.py. Consolidates ALL
# topology logic that used to be spread across derive_topology_subbasins.py,
# the prune, and write_basin.py into ONE validated pass, and writes a single
# explicit topology table that write_basin.py reads verbatim.
#
# CONNECTIVITY RULES
#   subbasin -> downstream : NEAREST junction to the subbasin's pour point.
#       Pour points are placed close to (but deliberately offset from) the
#       confluence junction -- offset so each branch delineates as its own
#       subwatershed -- so the single nearest junction is the correct outlet.
#       No fixed tolerance: nearest wins. The match distance is recorded for
#       audit so an unusually far match surfaces.
#   reach    -> downstream : the junction at the reach's downstream end (already
#       on reaches.gpkg from materialize_junctions), or the outlet -> Sink.
#   junction -> downstream : prefer its recorded ds_reach_id; if that attribute
#       is blank or incorrectly marked as outlet, recover the outgoing reach
#       spatially from a reach whose UP endpoint lies at the junction. This
#       prevents valid headwater/confluence junctions from being sent directly
#       to Sink merely because junction metadata is incomplete. True terminal
#       junction catchments are attached to their nearest feeder reach so the
#       OHQ writer can create a channel inflow without creating a graph cycle.
#
# PRUNE (internal headwater reaches)
#   A reach is INTERNAL (drop) if NOTHING drains into its UP end:
#       no surviving reach feeds it (through a junction at its up end), AND
#       no subbasin drains to a junction at its up end.
#   Such a reach is the channel internal to the subbasin that exits at the
#   junction below it; its routing is already in the subbasin transform.
#   The subbasin inflow ANCHORS every reach that has a subbasin at its head, so
#   pruning true stubs does not cascade. The outlet reach is never dropped.
#
# JUNCTION VALIDATION
#   total inflow = surviving reaches ending at it + subbasins assigned to it.
#   >=2 -> real confluence (keep). <2 -> collapse (bypass: feeders re-point to
#   the junction's downstream element).
#
# OUTPUT (in <SITE>/outputs/)
#   topology.gpkg, layer 'topology' (non-spatial attribute table):
#       element_id   int     numeric id within its type
#       element_type str     'subbasin' | 'reach' | 'junction' | 'sink'
#       name         str     HMS element name (Subbasin_7, Reach_83, ...)
#       ds_type      str     'junction' | 'reach' | 'sink'
#       ds_id        int     downstream element numeric id (NULL for sink)
#       ds_name      str     downstream HMS element name
#       match_dist_m double  subbasin->junction distance (NULL for non-subbasin)
#       note         str     audit text (e.g. 'far match', 'collapsed-through')
#
# After this, write_basin.py just reads topology.gpkg and emits blocks -- no
# topology logic in the writer.
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
PARAMS_NAME  = "subwatershed_params.gpkg"
PARAMS_LAYER = "subwatershed_params"
POURPTS_NAME = "pour_points.shp"

PIXEL_M = 9.336
SNAP_PX = 1.5                 # ONLY for "is an endpoint AT a junction" tests,
SNAP_TOL = SNAP_PX * PIXEL_M  # NOT for subbasin->junction (that is nearest-wins)
# Wider tolerance used only as a final recovery for slightly displaced endpoints.
RECOVERY_TOL_M = max(4.0 * PIXEL_M, 50.0)
FAR_MATCH_M = 60.0            # flag (do not reject) subbasin matches beyond this
SINK_NAME = "Outlet"
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
junc_p    = os.path.join(OUT_DIR, JUNC_NAME)
params_p  = os.path.join(OUT_DIR, PARAMS_NAME)
pourpts_p = os.path.join(OUT_DIR, POURPTS_NAME)
topo_p    = os.path.join(OUT_DIR, "topology.gpkg")

for p in (reaches_p, junc_p, params_p, pourpts_p):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

print("Reaches    :", reaches_p)
print("Junctions  :", junc_p)
print("Params     :", params_p)
print("Pour points:", pourpts_p)


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


def sub_name(i):  return "Subbasin_%s" % i
def rch_name(i):  return "Reach_%s" % i
def jct_name(i):  return "Junction_%s" % i

# ---------------------------------------------------------------------------
# load reaches (oriented up/dn), junctions, pour points, subbasins
# ---------------------------------------------------------------------------
reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer")
rfields = [f.name() for f in reaches.fields()]
has_dsj = "ds_junction_id" in rfields

R = {}     # reach_id -> dict(up, dn, ds_type, ds_reach_id, ds_junction_id)
for f in reaches.getFeatures():
    rid = iid(f["reach_id"])
    if rid is None:
        rid = int(f.id())
    fp, lp = line_endpoints(f.geometry())
    try:
        zu = float(f["z_up_m"]); zd = float(f["z_dn_m"]); swap = zd > zu
    except (TypeError, ValueError):
        swap = False
    up_pt, dn_pt = (lp, fp) if swap else (fp, lp)
    R[rid] = {
        "up": up_pt, "dn": dn_pt, "geom": f.geometry(),
        "ds_type": f["ds_type"],
        "ds_reach_id": iid(f["ds_reach_id"]),
        "ds_junction_id": iid(f["ds_junction_id"]) if has_dsj else None,
    }
print("\nReaches loaded :", len(R))

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
    J[jid] = {"pt": QgsPointXY(g.asPoint()),
              "ds_type": f["ds_type"],
              "ds_reach_id": iid(f["ds_reach_id"])}
print("Junctions loaded:", len(J))

pour = QgsVectorLayer(pourpts_p, "pour", "ogr")
if not pour.isValid():
    raise Exception("invalid pour-points layer")
PP = {}    # pour-point/subbasin id -> QgsPointXY
for f in pour.getFeatures():
    pid = iid(f["id"]) if "id" in [fl.name() for fl in pour.fields()] else int(f.id())
    g = f.geometry()
    if g is None or g.isEmpty():
        continue
    PP[pid] = QgsPointXY(g.asPoint())
print("Pour points loaded:", len(PP))

subs = QgsVectorLayer(params_p + "|layername=" + PARAMS_LAYER, "subs", "ogr")
if not subs.isValid():
    raise Exception("invalid subbasin layer")
SUB = set()
sub_cent = {}     # sid -> QgsPointXY centroid (for connector origin)
for f in subs.getFeatures():
    sid = iid(f["id"])
    if sid is not None:
        SUB.add(sid)
        try:
            cx = float(f["centroid_x"]); cy = float(f["centroid_y"])
            sub_cent[sid] = QgsPointXY(cx, cy)
        except (TypeError, ValueError):
            pass
print("Subbasins loaded:", len(SUB))

outlet_reaches = [rid for rid, r in R.items() if r["ds_type"] == "outlet"]
print("Outlet reach id(s):", outlet_reaches)

# ---------------------------------------------------------------------------
# helpers: which junction is at a point; nearest junction to a point
# ---------------------------------------------------------------------------
def junctions_at(pt):
    return [jid for jid in J if J[jid]["pt"].distance(pt) <= SNAP_TOL]

def nearest_junction(pt):
    best, bestd = None, None
    for jid, jd in J.items():
        d = pt.distance(jd["pt"])
        if bestd is None or d < bestd:
            best, bestd = jid, d
    return best, bestd

up_junc = {rid: junctions_at(R[rid]["up"]) for rid in R}
dn_junc = {rid: junctions_at(R[rid]["dn"]) for rid in R}

# ---------------------------------------------------------------------------
# STEP 1: subbasin -> nearest junction (nearest wins, distance recorded)
# ---------------------------------------------------------------------------
sub_ds_junc = {}   # sid -> junction_id
sub_ds_dist = {}   # sid -> distance (m)
for sid in SUB:
    if sid not in PP:
        sub_ds_junc[sid] = None; sub_ds_dist[sid] = None
        continue
    jid, d = nearest_junction(PP[sid])
    sub_ds_junc[sid] = jid; sub_ds_dist[sid] = d
print("\nSubbasin -> nearest junction assigned.")

# subbasins draining to each junction (for anchor test + junction validation)
subs_into_junc = {jid: [] for jid in J}
for sid, jid in sub_ds_junc.items():
    if jid is not None:
        subs_into_junc[jid].append(sid)

# ---------------------------------------------------------------------------
# STEP 2: prune internal headwater reaches
#   A reach is fed (KEEP) if its up end has an upstream feeder:
#     (a) a SUBBASIN drains to a junction at its up end, OR
#     (b) another surviving reach drains into a junction at its up end, OR
#     (c) a plain end-to-end seam: another reach's dn end at its up end.
#   The subbasin anchor (a) is permanent -> no cascade.
# ---------------------------------------------------------------------------
alive = set(R.keys())
dropped = []

# subbasin-anchored reaches: a subbasin drains to a junction at the reach up end
sub_anchored = {}
for rid in R:
    anchored = False
    for jid in up_junc[rid]:
        if subs_into_junc.get(jid):
            anchored = True
            break
    sub_anchored[rid] = anchored

def is_fed(rid, alive_set):
    if sub_anchored[rid]:
        return True
    for jid in up_junc[rid]:                      # (b) reach via junction
        for o in alive_set:
            if o != rid and jid in dn_junc[o]:
                return True
    up = R[rid]["up"]                              # (c) end-to-end seam
    for o in alive_set:
        if o != rid and R[o]["dn"].distance(up) <= SNAP_TOL:
            return True
    return False

while True:
    todo = [rid for rid in alive
            if rid not in outlet_reaches and not is_fed(rid, alive)]
    if not todo:
        break
    for rid in todo:
        alive.discard(rid); dropped.append(rid)

print("Internal reaches dropped:", sorted(dropped) if dropped else "none")
print("Surviving reaches       :", len(alive))

# ---------------------------------------------------------------------------
# STEP 3: junction validation (reaches + subbasins as inflow)
# ---------------------------------------------------------------------------
def reaches_into(jid):
    jp = J[jid]["pt"]
    return [rid for rid in alive if R[rid]["dn"].distance(jp) <= SNAP_TOL]

junc_rin  = {jid: reaches_into(jid) for jid in J}
junc_sin  = {jid: len(subs_into_junc.get(jid, [])) for jid in J}
keep_junc = set()
drop_junc = set()
for jid in J:
    if len(junc_rin[jid]) + junc_sin[jid] >= 2:
        keep_junc.add(jid)
    else:
        drop_junc.add(jid)
print("\nJunctions kept     :", sorted(keep_junc) if keep_junc else "none")
print("Junctions collapsed:", sorted(drop_junc) if drop_junc else "none")
for jid in sorted(J):
    print("    J%-3s reaches_in=%d subbasins_in=%d -> %s"
          % (jid, len(junc_rin[jid]), junc_sin[jid],
             "KEEP" if jid in keep_junc else "collapse"))

# ---------------------------------------------------------------------------
# STEP 4: resolve downstream targets (chase through collapsed junctions)
# ---------------------------------------------------------------------------
def outgoing_reaches_at_junction(jid, alive_only=False):
    """Return reaches whose UP endpoint is located at this junction.

    The junction layer's ``ds_reach_id`` is useful but is not always populated
    correctly.  Endpoint geometry is therefore the authoritative fallback.
    Incoming reaches are not selected because their DOWN endpoint, rather than
    their UP endpoint, lies at the junction.
    """
    candidates = []
    for rid in R:
        if alive_only and rid not in alive:
            continue
        if jid in up_junc.get(rid, []):
            candidates.append(rid)
    return sorted(candidates)


def junction_outflow_reach(jid):
    """Resolve the physical outgoing reach for a junction.

    Priority:
      1. valid junction ``ds_reach_id``;
      2. a downstream-reach reference carried by a reach entering the junction;
      3. a surviving reach whose UP endpoint is at the junction;
      4. any reach whose UP endpoint is at the junction;
      5. nearest UP endpoint within RECOVERY_TOL_M, excluding obvious incoming
         reaches whose DOWN endpoint is closer to the junction.

    ``ds_junction_id`` is intentionally NOT reversed to find an outflow: it
    identifies reaches entering a junction, not the reach leaving it.
    """
    recorded = J[jid].get("ds_reach_id")
    if recorded in R:
        return recorded, "recorded"

    # Metadata recovery: an incoming reach may explicitly identify the next
    # downstream reach even when junctions.gpkg omitted ds_reach_id.
    metadata_candidates = []
    for in_rid in junc_rin.get(jid, []):
        nxt = R[in_rid].get("ds_reach_id")
        if nxt in R and nxt != in_rid:
            metadata_candidates.append(nxt)
    metadata_candidates = sorted(set(metadata_candidates))
    if metadata_candidates:
        alive_meta = [rid for rid in metadata_candidates if rid in alive]
        chosen = (alive_meta or metadata_candidates)[0]
        if len(metadata_candidates) > 1:
            print("WARNING: Junction_%s has multiple metadata outflows %s; "
                  "using Reach_%s" % (jid, metadata_candidates, chosen))
        return chosen, "reach-metadata"

    alive_candidates = outgoing_reaches_at_junction(jid, alive_only=True)
    if alive_candidates:
        if len(alive_candidates) > 1:
            print("WARNING: Junction_%s has multiple surviving outgoing reaches %s; "
                  "using Reach_%s"
                  % (jid, alive_candidates, alive_candidates[0]))
        return alive_candidates[0], "spatial"

    all_candidates = outgoing_reaches_at_junction(jid, alive_only=False)
    if all_candidates:
        if len(all_candidates) > 1:
            print("WARNING: Junction_%s has multiple outgoing reaches %s; "
                  "using Reach_%s"
                  % (jid, all_candidates, all_candidates[0]))
        return all_candidates[0], "spatial-dropped"

    # Last-resort endpoint recovery for small offsets introduced by clipping,
    # raster/vector conversion, or provider precision. Never select an obvious
    # incoming reach as the outflow.
    jp = J[jid]["pt"]
    near = []
    for rid, rd in R.items():
        du = rd["up"].distance(jp)
        dd = rd["dn"].distance(jp)
        if du <= RECOVERY_TOL_M and du + 0.01 < dd:
            near.append((0 if rid in alive else 1, du, rid))
    near.sort()
    if near:
        _, dist, chosen = near[0]
        print("WARNING: Junction_%s outflow recovered by nearest UP endpoint: "
              "Reach_%s at %.2f m" % (jid, chosen, dist))
        return chosen, "nearest-up-endpoint"

    return None, "none"


def terminal_feeder_reach(jid, sid=None):
    """Choose a surviving reach entering a terminal junction.

    A subbasin whose pour point is assigned to a true terminal junction has no
    downstream channel after that junction. OpenHydroQual still needs the
    catchment attached to a channel block, so the catchment is attached to the
    nearest surviving feeder reach and that reach continues to the terminal
    junction/sink. This does not reverse the reach and does not create a cycle.
    """
    candidates = [rid for rid in junc_rin.get(jid, []) if rid in alive]
    if not candidates:
        return None
    p = PP.get(sid) if sid is not None else None
    if p is None:
        return sorted(candidates)[0]
    pg = QgsGeometry.fromPointXY(p)
    ranked = []
    for rid in candidates:
        try:
            d = R[rid]["geom"].distance(pg)
        except Exception:
            d = R[rid]["dn"].distance(p)
        ranked.append((d, rid))
    ranked.sort()
    return ranked[0][1]


def resolve_junction(jid, seen=None):
    if seen is None:
        seen = set()
    if ("J", jid) in seen:
        return ("sink", None)
    seen.add(("J", jid))

    if jid in keep_junc:
        return ("junction", jid)

    out_rid, source = junction_outflow_reach(jid)
    if out_rid is not None:
        return resolve_reach(out_rid, seen)

    # Only accept Sink after both metadata and spatial endpoint recovery fail.
    return ("sink", None)


def resolve_reach(rid, seen=None):
    if seen is None: seen = set()
    if ("R", rid) in seen: return ("sink", None)
    seen.add(("R", rid))
    if rid in alive:
        return ("reach", rid)
    r = R.get(rid)
    if r is None or r["ds_type"] == "outlet":
        return ("sink", None)
    if r["ds_type"] == "junction" and r["ds_junction_id"] is not None:
        return resolve_junction(r["ds_junction_id"], seen)
    if r["ds_reach_id"] is not None:
        return resolve_reach(r["ds_reach_id"], seen)
    return ("sink", None)

# reach downstream: the junction at its dn end (resolved), else outlet
reach_ds = {}     # rid -> (kind, id, note)
for rid in sorted(alive):
    if rid in outlet_reaches:
        reach_ds[rid] = ("sink", None, ""); continue
    at = [jid for jid in J if R[rid]["dn"].distance(J[jid]["pt"]) <= SNAP_TOL]
    # Prefer the reach layer's explicit downstream-junction id when it is one
    # of the spatial endpoint matches; otherwise use the nearest match.
    explicit_jid = R[rid].get("ds_junction_id")
    if explicit_jid in at:
        dn_jid = explicit_jid
    elif at:
        dn_jid = min(at, key=lambda jid: R[rid]["dn"].distance(J[jid]["pt"]))
    else:
        dn_jid = None

    if dn_jid is not None:
        kind, tgt = resolve_junction(dn_jid)
        note = "" if (dn_jid in keep_junc) else "via collapsed J%d" % dn_jid
    elif R[rid]["ds_type"] == "reach" and R[rid]["ds_reach_id"] is not None:
        kind, tgt = resolve_reach(R[rid]["ds_reach_id"]); note = ""
    else:
        kind, tgt = ("sink", None); note = ""
    reach_ds[rid] = (kind, tgt, note)

# junction downstream: recorded outflow reach, with spatial endpoint fallback
junc_ds = {}      # jid -> (kind, id, note)
for jid in sorted(keep_junc):
    out_rid, source = junction_outflow_reach(jid)
    if out_rid is None:
        junc_ds[jid] = ("sink", None, "no outgoing reach")
        continue

    kind, tgt = resolve_reach(out_rid)
    if source == "recorded":
        note = "" if kind == "reach" else "recorded outflow resolves to sink"
    elif kind == "reach":
        note = "outflow recovered spatially"
    else:
        note = "spatial outflow resolves to sink"
    junc_ds[jid] = (kind, tgt, note)

# subbasin downstream: its assigned junction if kept, else resolve through
sub_ds = {}       # sid -> (kind, id, note)
for sid in sorted(SUB):
    jid = sub_ds_junc.get(sid)
    note = ""
    d = sub_ds_dist.get(sid)
    if d is not None and d > FAR_MATCH_M:
        note = "far match %.0fm" % d
    if jid is None:
        sub_ds[sid] = ("sink", None, "no junction"); continue
    if jid in keep_junc:
        out_rid, _out_source = junction_outflow_reach(jid)
        if out_rid is not None:
            # Normal confluence: preserve the explicit subbasin -> junction ->
            # downstream-reach chain.
            sub_ds[sid] = ("junction", jid, note)
        else:
            # True terminal junction. Attach the catchment to the nearest
            # feeder channel so the OHQ writer can create a Catchment_link,
            # while the feeder reach still routes to this junction and Sink.
            feeder = terminal_feeder_reach(jid, sid)
            if feeder is not None:
                extra = "terminal J%d via feeder Reach_%d" % (jid, feeder)
                sub_ds[sid] = (
                    "reach", feeder,
                    (note + " " + extra).strip()
                )
            else:
                sub_ds[sid] = ("junction", jid,
                               (note + " terminal junction").strip())
    else:
        kind, tgt = resolve_junction(jid)
        sub_ds[sid] = (kind, tgt, (note + " via collapsed J%d" % jid).strip())

# ---------------------------------------------------------------------------
# STEP 5: validate the whole graph
# ---------------------------------------------------------------------------
def ds_name_of(kind, tid):
    if kind == "junction": return jct_name(tid)
    if kind == "reach":    return rch_name(tid)
    return SINK_NAME

# assemble element -> downstream-name map for graph walk
graph = {}   # name -> ds_name
for sid in SUB:
    k, t, _ = sub_ds[sid]; graph[sub_name(sid)] = ds_name_of(k, t)
for rid in alive:
    k, t, _ = reach_ds[rid]; graph[rch_name(rid)] = ds_name_of(k, t)
for jid in keep_junc:
    k, t, _ = junc_ds[jid]; graph[jct_name(jid)] = ds_name_of(k, t)
graph[SINK_NAME] = None

problems = []
# dangling
for n, ds in graph.items():
    if ds is not None and ds not in graph:
        problems.append("DANGLING: %s -> %s" % (n, ds))
# reachability + cycles
def walk(n):
    seen = []
    while n is not None:
        if n in seen: return ("CYCLE", seen + [n])
        seen.append(n); n = graph.get(n)
    return ("OK", seen)
sinks_reached = 0
for n in graph:
    if n == SINK_NAME: continue
    kind, path = walk(n)
    if kind == "CYCLE":
        problems.append("CYCLE: %s" % " -> ".join(path))
    elif path[-1] != SINK_NAME:
        problems.append("NO SINK: %s ends at %s" % (n, path[-1]))
# junctions >=2 in (already enforced) and exactly one sink
nsink = sum(1 for n in graph if n == SINK_NAME)

print("\nGRAPH VALIDATION")
if problems:
    for p in problems: print("  PROBLEM:", p)
else:
    print("  OK: single sink, no cycles, all elements reach", SINK_NAME)
print("  sink count:", nsink, "(expect 1)")

# ---------------------------------------------------------------------------
# STEP 6: write topology.gpkg (non-spatial attribute table)
# ---------------------------------------------------------------------------
flds = QgsFields()
flds.append(QgsField("element_id",   QVariant.Int))
flds.append(QgsField("element_type", QVariant.String))
flds.append(QgsField("name",         QVariant.String))
flds.append(QgsField("ds_type",      QVariant.String))
flds.append(QgsField("ds_id",        QVariant.Int))
flds.append(QgsField("ds_name",      QVariant.String))
flds.append(QgsField("match_dist_m", QVariant.Double))
flds.append(QgsField("note",         QVariant.String))

# release lock if loaded
_proj = QgsProject.instance()
for _lyr in list(_proj.mapLayers().values()):
    try:
        _src = _lyr.source().split("|", 1)[0]
    except Exception:
        _src = ""
    if os.path.normcase(os.path.abspath(_src)) == \
       os.path.normcase(os.path.abspath(topo_p)):
        _proj.removeMapLayer(_lyr.id())

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"; opts.layerName = "topology"
opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
w = QgsVectorFileWriter.create(topo_p, flds, QgsWkbTypes.NoGeometry,
                               reaches.crs(), QgsCoordinateTransformContext(), opts)

def emit(eid, etype, name, k, t, dist, note):
    ft = QgsFeature(flds)
    ft["element_id"]   = int(eid)
    ft["element_type"] = etype
    ft["name"]         = name
    ft["ds_type"]      = k
    ft["ds_id"]        = None if t is None else int(t)
    ft["ds_name"]      = ds_name_of(k, t)
    ft["match_dist_m"] = None if dist is None else float(dist)
    ft["note"]         = note or ""
    w.addFeature(ft)

for sid in sorted(SUB):
    k, t, note = sub_ds[sid]
    emit(sid, "subbasin", sub_name(sid), k, t, sub_ds_dist.get(sid), note)
for rid in sorted(alive):
    k, t, note = reach_ds[rid]
    emit(rid, "reach", rch_name(rid), k, t, None, note)
for jid in sorted(keep_junc):
    k, t, note = junc_ds[jid]
    emit(jid, "junction", jct_name(jid), k, t, None, note)
emit(0, "sink", SINK_NAME, "sink", None, None, "")
del w
print("\nWrote topology ->", topo_p)

# ---------------------------------------------------------------------------
# STEP 6b: write topology_connectors.gpkg -- one LineString per wiring link,
# from each element's representative point to its downstream element's point.
# This is the VISUAL verification layer (style by 'kind'); the table above is
# the authoritative source write_basin reads. Both come from the same resolved
# topology so they cannot drift.
#   representative points:
#     subbasin  -> centroid (origin)         ; reach -> its dn end
#     junction  -> junction point            ; sink  -> outlet reach dn end
# ---------------------------------------------------------------------------
connect_p = os.path.join(OUT_DIR, "topology_connectors.gpkg")

# point lookups for downstream targets
def target_point(kind, tid):
    if kind == "junction":
        return J[tid]["pt"]
    if kind == "reach":
        return R[tid]["dn"] if tid in R else None
    # sink: outlet reach dn end (or None)
    if outlet_reaches:
        return R[outlet_reaches[0]]["dn"]
    return None

cflds = QgsFields()
cflds.append(QgsField("kind",    QVariant.String))   # subbasin|reach|junction
cflds.append(QgsField("src",     QVariant.String))   # source element name
cflds.append(QgsField("dst",     QVariant.String))   # downstream element name
cflds.append(QgsField("dist_m",  QVariant.Double))
cflds.append(QgsField("note",    QVariant.String))

for _lyr in list(_proj.mapLayers().values()):
    try:
        _src = _lyr.source().split("|", 1)[0]
    except Exception:
        _src = ""
    if os.path.normcase(os.path.abspath(_src)) == \
       os.path.normcase(os.path.abspath(connect_p)):
        _proj.removeMapLayer(_lyr.id())

copts = QgsVectorFileWriter.SaveVectorOptions()
copts.driverName = "GPKG"; copts.layerName = "topology_connectors"
copts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
cw = QgsVectorFileWriter.create(connect_p, cflds, QgsWkbTypes.LineString,
                                reaches.crs(), QgsCoordinateTransformContext(), copts)

def add_connector(kind, src_pt, src_name, ds_kind, ds_id, note):
    if src_pt is None:
        return False
    b = target_point(ds_kind, ds_id)
    if b is None or src_pt.distance(b) < 0.01:
        return False
    ft = QgsFeature(cflds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([src_pt, b]))
    ft["kind"]   = kind
    ft["src"]    = src_name
    ft["dst"]    = ds_name_of(ds_kind, ds_id)
    ft["dist_m"] = src_pt.distance(b)
    ft["note"]   = note or ""
    cw.addFeature(ft)
    return True

n_conn = 0
for sid in sorted(SUB):
    k, t, note = sub_ds[sid]
    if add_connector("subbasin", sub_cent.get(sid), sub_name(sid), k, t, note):
        n_conn += 1
for rid in sorted(alive):
    k, t, note = reach_ds[rid]
    if add_connector("reach", R[rid]["dn"], rch_name(rid), k, t, note):
        n_conn += 1
for jid in sorted(keep_junc):
    k, t, note = junc_ds[jid]
    if add_connector("junction", J[jid]["pt"], jct_name(jid), k, t, note):
        n_conn += 1
del cw
print("Wrote %d connector line(s) -> %s" % (n_conn, connect_p))

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
print("\nTOPOLOGY (element -> downstream)")
for sid in sorted(SUB):
    k, t, note = sub_ds[sid]
    print("  %-13s -> %-13s %s" % (sub_name(sid), ds_name_of(k, t),
                                   ("[%s]" % note) if note else ""))
for rid in sorted(alive):
    k, t, note = reach_ds[rid]
    print("  %-13s -> %-13s %s" % (rch_name(rid), ds_name_of(k, t),
                                   ("[%s]" % note) if note else ""))
for jid in sorted(keep_junc):
    k, t, note = junc_ds[jid]
    print("  %-13s -> %-13s (%d in) %s"
          % (jct_name(jid), ds_name_of(k, t),
             len(junc_rin[jid]) + junc_sin[jid], ("[%s]" % note) if note else ""))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(topo_p + "|layername=topology", "topology", "ogr"))
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(connect_p, "topology_connectors", "ogr"))
    print("\n  loaded topology table + topology_connectors")

print("\nDone. topology.gpkg is the single source of truth; write_basin.py")
print("reads it verbatim. VERIFY the report above: every junction >=2 in,")
print("one sink, dropped reaches are the intended internal stubs.")

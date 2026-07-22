# =============================================================================
# materialize_junctions.py   (QGIS Python Console)
#
# Create explicit junction elements at stream confluences and rewire reach
# downstream pointers through those junctions.
#
# Run AFTER derive_topology.py and BEFORE build_topology.py.
#
# IMPORTANT FIXES IN THIS VERSION
#   1. Uses reach_id values consistently. The previous implementation mixed
#      provider feature IDs (f.id()) with reach_id attributes, which can create
#      invalid ds_reach_id references whenever those values differ.
#   2. Detects confluences from clusters containing at least two DOWNSTREAM
#      reach ends. It no longer requires three perfectly coincident endpoints.
#   3. Recovers a junction outflow geometrically when the downstream reach's
#      upstream endpoint falls just outside the primary clustering tolerance.
#   4. Uses existing derive_topology.py downstream pointers as a second source
#      of truth when geometry alone is ambiguous.
#   5. Marks a junction as outlet only when no valid outgoing reach can be
#      recovered from either geometry or existing downstream metadata.
#   6. Writes verification connectors using reach_id values consistently.
#
# OUTPUTS (in <SITE>/outputs/)
#   reaches.gpkg
#       ds_type may become 'junction'
#       ds_reach_id cleared for reaches entering a junction
#       ds_junction_id set for reaches entering a junction
#
#   junctions.gpkg, layer 'junctions'
#       junction_id, x, y, ds_type, ds_reach_id, n_ends, n_in
#
#   topology_connectors.gpkg, layer 'topology_connectors'
#       visual verification links through junctions
#
# Run from QGIS Python Console.
# =============================================================================

import os
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFields,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsVectorFileWriter,
    QgsWkbTypes,
    QgsCoordinateTransformContext,
)
from qgis.PyQt.QtCore import QVariant

# --- settings --------------------------------------------------------------
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

REACHES_NAME = "reaches.gpkg"

SNAP_PX = float(globals().get("SNAP_PX", 1.5))
PIXEL_M = globals().get("PIXEL_M", None)
SNAP_TOL = globals().get("SNAP_TOL", None)

ORIENTATION_REVERSAL_MIN_RISE_M = float(
    globals().get("ORIENTATION_REVERSAL_MIN_RISE_M", 0.25)
)
ORIENTATION_REVERSAL_MIN_SLOPE = float(
    globals().get("ORIENTATION_REVERSAL_MIN_SLOPE", 0.00010)
)

# Used only to recover a downstream reach when its upstream endpoint is not in
# the primary confluence cluster. Keep this modest so unrelated nearby reaches
# are not joined accidentally.
OUTFLOW_SEARCH_FACTOR = float(globals().get("OUTFLOW_SEARCH_FACTOR", 2.5))

RELOAD_IN_PROJECT = bool(globals().get("RELOAD_IN_PROJECT", True))
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
junc_p = os.path.join(OUT_DIR, "junctions.gpkg")
connect_p = os.path.join(OUT_DIR, "topology_connectors.gpkg")
flowdir_p = os.path.join(OUT_DIR, "flow_dir.tif")

if PIXEL_M is None:
    try:
        from qgis.core import QgsRasterLayer
        _raster = QgsRasterLayer(flowdir_p, "flow_dir_for_junctions")
        if _raster.isValid():
            _px_values = [
                abs(float(_raster.rasterUnitsPerPixelX())),
                abs(float(_raster.rasterUnitsPerPixelY())),
            ]
            _px_values = [v for v in _px_values if v > 0]
            PIXEL_M = sum(_px_values) / len(_px_values) if _px_values else 9.336
        else:
            PIXEL_M = 9.336
    except Exception:
        PIXEL_M = 9.336

PIXEL_M = float(PIXEL_M)
SNAP_TOL = float(SNAP_TOL) if SNAP_TOL is not None else SNAP_PX * PIXEL_M
OUTFLOW_SEARCH_TOL = OUTFLOW_SEARCH_FACTOR * SNAP_TOL

if not os.path.isfile(reaches_p):
    raise Exception("not found: " + reaches_p)

reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer")

field_names = [f.name() for f in reaches.fields()]
if "ds_reach_id" not in field_names:
    raise Exception("run derive_topology.py first (no ds_reach_id field)")


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
    if geom is None or geom.isEmpty():
        return None, None

    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    valid = [part for part in parts if len(part) >= 2]
    if not valid:
        return None, None

    def part_length(part):
        total = 0.0
        for i in range(1, len(part)):
            total += QgsPointXY(part[i - 1]).distance(QgsPointXY(part[i]))
        return total

    pts = max(valid, key=part_length)
    return QgsPointXY(pts[0]), QgsPointXY(pts[-1])


def normalized_text(v):
    if v is None:
        return ""
    return str(v).strip().lower()


# ---------------------------------------------------------------------------
# Load reaches using reach_id as the authoritative identifier.
# ---------------------------------------------------------------------------
rinfo = {}          # reach_id -> metadata
fid_to_rid = {}     # provider feature id -> reach_id
rid_to_fid = {}     # reach_id -> provider feature id

has_reach_id = "reach_id" in field_names
has_z_up = "z_up_m" in field_names
has_z_dn = "z_dn_m" in field_names
has_ds_type = "ds_type" in field_names

for f in reaches.getFeatures():
    rid = iid(f["reach_id"]) if has_reach_id else iid(f.id())
    if rid is None:
        raise Exception("reach feature %s has no usable reach_id" % f.id())
    if rid in rinfo:
        raise Exception("duplicate reach_id found: %s" % rid)

    fp, lp = line_endpoints(f.geometry())
    if fp is None or lp is None:
        print("WARNING: skipping empty/invalid reach geometry, reach_id=%s" % rid)
        continue

    # Use the orientation decision written by derive_topology_reaches.py when
    # available. Otherwise repeat its conservative rule: preserve GRASS geometry
    # order unless a materially large adverse rise and slope both support reversal.
    orient_text = normalized_text(f["topo_orient"]) if "topo_orient" in field_names else ""
    swap = orient_text == "strong-elevation-reversal"

    if not orient_text and has_z_up and has_z_dn:
        try:
            zu = float(f["z_up_m"])
            zd = float(f["z_dn_m"])
            length_m = float(f.geometry().length())
            adverse_rise = zd - zu
            adverse_slope = adverse_rise / length_m if length_m > 0 else 0.0
            swap = (
                adverse_rise >= ORIENTATION_REVERSAL_MIN_RISE_M
                and adverse_slope >= ORIENTATION_REVERSAL_MIN_SLOPE
            )
        except (TypeError, ValueError):
            swap = False

    up_pt, dn_pt = (lp, fp) if swap else (fp, lp)
    ds_type = normalized_text(f["ds_type"]) if has_ds_type else ""
    ds_reach_id = iid(f["ds_reach_id"])

    rinfo[rid] = {
        "fid": int(f.id()),
        "up": up_pt,
        "dn": dn_pt,
        "ds_type": ds_type,
        "ds_reach_id": ds_reach_id,
    }
    fid_to_rid[int(f.id())] = rid
    rid_to_fid[rid] = int(f.id())

print("Reaches:", len(rinfo))
print("Primary snap tolerance: %.2f m" % SNAP_TOL)
print("Outflow recovery tolerance: %.2f m" % OUTFLOW_SEARCH_TOL)

if not rinfo:
    raise Exception("no usable reaches loaded")


# ---------------------------------------------------------------------------
# Cluster reach endpoints.
#
# A true confluence candidate requires at least two downstream reach ends.
# Upstream ends may or may not fall inside the same cluster.
# ---------------------------------------------------------------------------
ends = []
for rid, info in rinfo.items():
    ends.append((rid, "dn", info["dn"]))
    ends.append((rid, "up", info["up"]))

clusters = []
for rid, role, p in ends:
    best_i = None
    best_d = None
    for i, c in enumerate(clusters):
        d = c["pt"].distance(p)
        if d <= SNAP_TOL and (best_d is None or d < best_d):
            best_i = i
            best_d = d

    if best_i is None:
        clusters.append({
            "pt": QgsPointXY(p.x(), p.y()),
            "members": [(rid, role, p)],
        })
    else:
        c = clusters[best_i]
        c["members"].append((rid, role, p))
        n = len(c["members"])
        c["pt"] = QgsPointXY(
            (c["pt"].x() * (n - 1) + p.x()) / n,
            (c["pt"].y() * (n - 1) + p.y()) / n,
        )

candidate_clusters = []
for c in clusters:
    ins = sorted({rid for rid, role, _ in c["members"] if role == "dn"})
    outs = sorted({rid for rid, role, _ in c["members"] if role == "up"})
    if len(ins) >= 2:
        candidate_clusters.append({
            "pt": c["pt"],
            "members": c["members"],
            "ins": ins,
            "cluster_outs": outs,
        })

print("Endpoint clusters:", len(clusters))
print("Confluence candidates (>=2 downstream ends):", len(candidate_clusters))


# ---------------------------------------------------------------------------
# Recover the unique outgoing reach for each confluence.
# ---------------------------------------------------------------------------
def metadata_outflow_candidates(incoming_rids):
    """Return downstream reach IDs referenced by incoming reaches."""
    vals = []
    for rid in incoming_rids:
        info = rinfo[rid]
        ds = info["ds_reach_id"]
        if info["ds_type"] == "reach" and ds in rinfo and ds not in incoming_rids:
            vals.append(ds)
    return sorted(set(vals))


def nearby_upstream_candidates(pt, incoming_rids, tolerance):
    vals = []
    for rid, info in rinfo.items():
        if rid in incoming_rids:
            continue
        d = pt.distance(info["up"])
        if d <= tolerance:
            vals.append((d, rid))
    vals.sort(key=lambda x: (x[0], x[1]))
    return vals


def choose_outflow(pt, incoming_rids, cluster_outs):
    incoming = set(incoming_rids)

    # 1. Exact/primary-cluster upstream endpoint candidates.
    exact = sorted(rid for rid in set(cluster_outs) if rid not in incoming)
    if len(exact) == 1:
        return exact[0], "cluster"

    # 2. Existing derive_topology pointers from incoming reaches.
    meta = metadata_outflow_candidates(incoming_rids)
    if len(meta) == 1:
        return meta[0], "metadata"

    # If exact candidates are ambiguous, prefer one supported by metadata.
    if len(exact) > 1 and meta:
        supported = [rid for rid in exact if rid in meta]
        if len(supported) == 1:
            return supported[0], "cluster+metadata"

    # 3. Geometric recovery with a relaxed but bounded tolerance.
    nearby = nearby_upstream_candidates(pt, incoming, OUTFLOW_SEARCH_TOL)
    if nearby:
        # Prefer a nearby candidate also supported by metadata.
        if meta:
            supported = [(d, rid) for d, rid in nearby if rid in meta]
            if supported:
                return supported[0][1], "nearby+metadata %.2fm" % supported[0][0]

        # Otherwise take the nearest candidate only when clearly separated.
        if len(nearby) == 1:
            return nearby[0][1], "nearby %.2fm" % nearby[0][0]

        d0, r0 = nearby[0]
        d1, _ = nearby[1]
        if d0 + 0.25 * SNAP_TOL < d1:
            return r0, "nearest %.2fm" % d0

    # 4. If multiple metadata candidates remain, choose the nearest upstream
    # endpoint among them and flag the ambiguity.
    if len(meta) > 1:
        ranked = sorted((pt.distance(rinfo[rid]["up"]), rid) for rid in meta)
        return ranked[0][1], "ambiguous metadata; nearest %.2fm" % ranked[0][0]

    # No valid downstream reach could be recovered.
    return None, "outlet/no downstream reach"


reach_to_junc = {}     # incoming reach_id -> junction_id
junc_outflow = {}      # junction_id -> outgoing reach_id or None
junc_note = {}         # junction_id -> audit note
junctions = []         # (jid, point, n_ends, n_in)

for jid, c in enumerate(candidate_clusters, start=1):
    ins = c["ins"]
    out, note = choose_outflow(c["pt"], ins, c["cluster_outs"])

    junctions.append((jid, c["pt"], len(c["members"]), len(ins)))
    junc_outflow[jid] = out
    junc_note[jid] = note

    for rid in ins:
        # A reach downstream end should belong to only one junction. If the
        # endpoint clustering produced an overlap, keep the first and report it.
        if rid in reach_to_junc:
            print(
                "WARNING: Reach_%d assigned to more than one junction; "
                "keeping J%d and ignoring J%d"
                % (rid, reach_to_junc[rid], jid)
            )
            continue
        reach_to_junc[rid] = jid

    if out is None:
        print("  J%d: %s" % (jid, note))
    else:
        print("  J%d: incoming=%s -> Reach_%d [%s]" % (jid, ins, out, note))


# ---------------------------------------------------------------------------
# Ensure output reach is not also treated as an incoming reach at that same
# junction due to noisy clustering.
# ---------------------------------------------------------------------------
for jid, out in list(junc_outflow.items()):
    if out is None:
        continue
    if reach_to_junc.get(out) == jid:
        del reach_to_junc[out]
        print(
            "WARNING J%d: Reach_%d appeared as both inflow and outflow; "
            "kept it as outflow." % (jid, out)
        )


# ---------------------------------------------------------------------------
# Write ds_junction_id onto reaches and rewire incoming reaches.
# ---------------------------------------------------------------------------
reaches.startEditing()
current_fields = [f.name() for f in reaches.fields()]
if "ds_junction_id" not in current_fields:
    reaches.dataProvider().addAttributes([
        QgsField("ds_junction_id", QVariant.Int)
    ])
    reaches.updateFields()

idx_dst = reaches.fields().indexFromName("ds_type")
idx_dsr = reaches.fields().indexFromName("ds_reach_id")
idx_dsj = reaches.fields().indexFromName("ds_junction_id")

if idx_dst < 0 or idx_dsr < 0 or idx_dsj < 0:
    reaches.rollBack()
    raise Exception("required downstream fields are missing from reaches.gpkg")

for f in reaches.getFeatures():
    rid = fid_to_rid.get(int(f.id()))
    if rid is None:
        continue

    if rid in reach_to_junc:
        jid = reach_to_junc[rid]
        reaches.changeAttributeValue(f.id(), idx_dst, "junction")
        reaches.changeAttributeValue(f.id(), idx_dsr, None)
        reaches.changeAttributeValue(f.id(), idx_dsj, int(jid))
    else:
        # Preserve derive_topology's reach/outlet pointer and clear stale J id.
        reaches.changeAttributeValue(f.id(), idx_dsj, None)

if not reaches.commitChanges():
    reaches.rollBack()
    raise Exception("failed to commit junction rewiring to reaches.gpkg")

print("Rewired %d reach(es) to junctions." % len(reach_to_junc))


# ---------------------------------------------------------------------------
# Write junctions.gpkg.
# ---------------------------------------------------------------------------
flds = QgsFields()
flds.append(QgsField("junction_id", QVariant.Int))
flds.append(QgsField("x", QVariant.Double))
flds.append(QgsField("y", QVariant.Double))
flds.append(QgsField("ds_type", QVariant.String))
flds.append(QgsField("ds_reach_id", QVariant.Int))
flds.append(QgsField("n_ends", QVariant.Int))
flds.append(QgsField("n_in", QVariant.Int))
flds.append(QgsField("note", QVariant.String))

proj = QgsProject.instance()
for lyr in list(proj.mapLayers().values()):
    try:
        src = lyr.source().split("|", 1)[0]
    except Exception:
        src = ""
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(junc_p)):
        proj.removeMapLayer(lyr.id())

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"
opts.layerName = "junctions"
opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

writer = QgsVectorFileWriter.create(
    junc_p,
    flds,
    QgsWkbTypes.Point,
    reaches.crs(),
    QgsCoordinateTransformContext(),
    opts,
)

if writer.hasError() != QgsVectorFileWriter.NoError:
    raise Exception("failed to create junctions.gpkg: " + writer.errorMessage())

for jid, pt, n_ends, n_in in junctions:
    out = junc_outflow.get(jid)
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPointXY(pt))
    ft["junction_id"] = int(jid)
    ft["x"] = float(pt.x())
    ft["y"] = float(pt.y())
    ft["ds_type"] = "outlet" if out is None else "reach"
    ft["ds_reach_id"] = None if out is None else int(out)
    ft["n_ends"] = int(n_ends)
    ft["n_in"] = int(n_in)
    ft["note"] = junc_note.get(jid, "")
    writer.addFeature(ft)

del writer
print("Wrote", os.path.basename(junc_p), "with", len(junctions), "junction(s)")


# ---------------------------------------------------------------------------
# Rewrite topology_connectors.gpkg for visual verification.
# ---------------------------------------------------------------------------
cflds = QgsFields()
cflds.append(QgsField("kind", QVariant.String))
cflds.append(QgsField("src", QVariant.Int))
cflds.append(QgsField("dst", QVariant.Int))
cflds.append(QgsField("note", QVariant.String))

for lyr in list(proj.mapLayers().values()):
    try:
        src = lyr.source().split("|", 1)[0]
    except Exception:
        src = ""
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(connect_p)):
        proj.removeMapLayer(lyr.id())

opts2 = QgsVectorFileWriter.SaveVectorOptions()
opts2.driverName = "GPKG"
opts2.layerName = "topology_connectors"
opts2.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

cw = QgsVectorFileWriter.create(
    connect_p,
    cflds,
    QgsWkbTypes.LineString,
    reaches.crs(),
    QgsCoordinateTransformContext(),
    opts2,
)

if cw.hasError() != QgsVectorFileWriter.NoError:
    raise Exception("failed to create topology_connectors.gpkg: " + cw.errorMessage())

jpt = {jid: pt for jid, pt, _, _ in junctions}

# Incoming reach -> junction.
for rid, jid in sorted(reach_to_junc.items()):
    a = rinfo[rid]["dn"]
    b = jpt[jid]
    ft = QgsFeature(cflds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([a, b]))
    ft["kind"] = "reach_in"
    ft["src"] = int(rid)
    ft["dst"] = int(jid)
    ft["note"] = ""
    cw.addFeature(ft)

# Junction -> outgoing reach.
for jid, out in sorted(junc_outflow.items()):
    if out is None or out not in rinfo:
        continue
    a = jpt[jid]
    b = rinfo[out]["up"]
    ft = QgsFeature(cflds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([a, b]))
    ft["kind"] = "junc_out"
    ft["src"] = int(jid)
    ft["dst"] = int(out)
    ft["note"] = junc_note.get(jid, "")
    cw.addFeature(ft)

# Direct reach -> reach links that were not rewired through a junction.
reaches_check = QgsVectorLayer(reaches_p, "reaches_check", "ogr")
if not reaches_check.isValid():
    del cw
    raise Exception("could not reopen reaches.gpkg after rewiring")

for f in reaches_check.getFeatures():
    rid = iid(f["reach_id"]) if has_reach_id else iid(f.id())
    if rid is None or rid in reach_to_junc:
        continue

    ds_type = normalized_text(f["ds_type"])
    ds = iid(f["ds_reach_id"])
    if ds_type != "reach" or ds is None or ds not in rinfo:
        continue

    ft = QgsFeature(cflds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([
        rinfo[rid]["dn"],
        rinfo[ds]["up"],
    ]))
    ft["kind"] = "reach_direct"
    ft["src"] = int(rid)
    ft["dst"] = int(ds)
    ft["note"] = ""
    cw.addFeature(ft)

del cw
print("Rewrote", os.path.basename(connect_p), "through junctions")


# ---------------------------------------------------------------------------
# Validation report.
# ---------------------------------------------------------------------------
print("\nJUNCTION SUMMARY")
for jid, pt, n_ends, n_in in junctions:
    out = junc_outflow.get(jid)
    out_text = "OUTLET" if out is None else "Reach_%d" % out
    print(
        "  J%-3d ends=%-2d in=%-2d out=%-12s @ (%.1f, %.1f) [%s]"
        % (
            jid,
            n_ends,
            n_in,
            out_text,
            pt.x(),
            pt.y(),
            junc_note.get(jid, ""),
        )
    )

# Check references before handing control to build_topology.py.
problems = []
for jid, out in junc_outflow.items():
    if out is not None and out not in rinfo:
        problems.append("Junction_%d references missing Reach_%d" % (jid, out))

for rid, jid in reach_to_junc.items():
    if jid not in jpt:
        problems.append("Reach_%d references missing Junction_%d" % (rid, jid))

print("\nVALIDATION")
if problems:
    for p in problems:
        print("  PROBLEM:", p)
    raise Exception("junction materialization failed validation")
else:
    print("  OK: all reach and junction references use valid reach_id values")

if RELOAD_IN_PROJECT:
    proj.addMapLayer(QgsVectorLayer(junc_p + "|layername=junctions", "junctions", "ogr"))
    proj.addMapLayer(QgsVectorLayer(
        connect_p + "|layername=topology_connectors",
        "topology_connectors",
        "ogr",
    ))

print("\nDone. Inspect junctions and topology_connectors over reaches.")
print("Then rerun build_topology.py and regenerate the OHQ model.")

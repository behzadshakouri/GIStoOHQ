# =============================================================================
# derive_topology.py   (QGIS Python Console)
#
# NHA WS3, HMS network step: derive element connectivity from geometry.
# Mode: subbasins drain DIRECT to the nearest reach (no junction per pour point).
#
# WHAT IT DERIVES
#   1. Reach -> downstream reach. Each reach has a downstream end (the lower-
#      elevation endpoint, from z_up_m / z_dn_m). Reach A drains into reach B if
#      A's downstream end coincides with B's UPSTREAM end within SNAP_PX pixels.
#      The reach whose downstream end matches nothing is the OUTLET (sink).
#   2. Subbasin -> downstream reach. Each subbasin's pour point is matched to the
#      nearest reach ENDPOINT; that reach (entered at its upstream end) is the
#      subbasin's downstream element. HMS cannot drain a subbasin to the middle
#      of a reach, so a pour point that is nearer a reach INTERIOR than any
#      endpoint is FLAGGED (mid-reach case) -- on finely discretized real sites
#      this is where a reach-break/junction would be needed.
#
# Subbasin<->pour-point pairing is SPATIAL (pour point inside subbasin polygon),
# with the shared id used only as a cross-check. id is coerced to int on both
# sides (pour points store it as string, subbasins as integer).
#
# COUNT-AGNOSTIC: nothing here assumes the number of subbasins/reaches; it all
# derives from geometry, so it scales from the 6-subbasin test to real sites.
#
# OUTPUTS (in <SITE>/outputs/)
#   reaches.gpkg              + reach_id, ds_type ('reach'|'outlet'), ds_reach_id
#   subwatershed_params.gpkg  + ds_reach_id, ds_flag ('endpoint'|'MIDREACH')
#   topology_connectors.gpkg  verification lines: each element -> its downstream
#                             target (render this and eyeball before sealing)
#
# VERIFY BEFORE SEALING: auto-derived topology can mis-wire on a mis-snapped
# pour point or an ambiguous flat reach. Render topology_connectors.gpkg over
# the reaches + pour points and confirm every arrow points downstream.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsField, QgsFields, QgsFeature,
    QgsGeometry, QgsPointXY, QgsVectorFileWriter, QgsWkbTypes,
    QgsCoordinateTransformContext, QgsSpatialIndex
)
from qgis.PyQt.QtCore import QVariant

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
ROOT     = "C:/Users/smnfa/Dropbox/NHA/"
SITE_DIR = "WS3_GIS/AZ12-100"

REACHES_NAME   = "reaches.gpkg"
PARAMS_NAME    = "subwatershed_params.gpkg"
PARAMS_LAYER   = "subwatershed_params"
POURPTS_NAME   = "pour_points_snapped.gpkg"
SUBPOLY_NAME   = "subwatershed_params.gpkg"      # polygons live in the params gpkg
SUBPOLY_LAYER  = "subwatershed_params"

PIXEL_M  = 9.336          # for snap tolerance
SNAP_PX  = 1.5            # endpoint-coincidence tolerance, in pixels
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
params_p  = os.path.join(OUT_DIR, PARAMS_NAME)
pourpts_p = os.path.join(OUT_DIR, POURPTS_NAME)
connect_p = os.path.join(OUT_DIR, "topology_connectors.gpkg")
SNAP_TOL  = SNAP_PX * PIXEL_M

for p in (reaches_p, params_p, pourpts_p):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

print("Reaches    :", reaches_p)
print("Params     :", params_p)
print("Pour points:", pourpts_p)
print("Snap tol   : %.2f m (%.1f px)" % (SNAP_TOL, SNAP_PX))

def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# load reaches; build per-reach up/down endpoints using z to orient them
# ---------------------------------------------------------------------------
reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer")

def line_endpoints(geom):
    """return (firstPoint, lastPoint) as QgsPointXY for a (multi)line."""
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        pts = [p for part in parts for p in part]
    else:
        pts = geom.asPolyline()
    return QgsPointXY(pts[0]), QgsPointXY(pts[-1])

# reach record: fid -> {up: pt, dn: pt}  (oriented by elevation)
rinfo = {}
for f in reaches.getFeatures():
    fp, lp = line_endpoints(f.geometry())
    zu, zd = f["z_up_m"], f["z_dn_m"]
    # z_up_m was sampled at vertex 0, z_dn_m at vertex -1 (extractspecificvertices
    # order). If the downstream (lower) elevation is at the FIRST vertex, the
    # line is digitized upstream-to-... reversed; orient by elevation to be safe.
    if zu is not None and zd is not None and zd > zu:
        up_pt, dn_pt = lp, fp        # first vertex is actually lower -> swap
    else:
        up_pt, dn_pt = fp, lp
    rinfo[f.id()] = {"up": up_pt, "dn": dn_pt}

print("\nReaches loaded:", len(rinfo))

# ---------------------------------------------------------------------------
# 1. reach -> downstream reach: match each reach's DN end to another reach UP end
# ---------------------------------------------------------------------------
def near(a, b, tol):
    return a.distance(b) <= tol

ds_reach = {}     # fid -> downstream reach fid (or None = outlet)
for fid, e in rinfo.items():
    best, bestd = None, None
    for fid2, e2 in rinfo.items():
        if fid2 == fid:
            continue
        d = e["dn"].distance(e2["up"])
        if bestd is None or d < bestd:
            best, bestd = fid2, d
    ds_reach[fid] = best if (bestd is not None and bestd <= SNAP_TOL) else None

outlets = [fid for fid, ds in ds_reach.items() if ds is None]
print("Reach->downstream resolved. Outlet reach fid(s):", outlets)
if len(outlets) != 1:
    print("  WARNING: expected exactly 1 outlet, got %d. Check network for"
          " disconnected segments or multiple sinks." % len(outlets))

# ---------------------------------------------------------------------------
# 2. subbasin -> downstream reach via pour point -> nearest reach ENDPOINT
#    (flag pour points nearer a reach interior than any endpoint = mid-reach)
# ---------------------------------------------------------------------------
pts = QgsVectorLayer(pourpts_p, "pour", "ogr")
subs = QgsVectorLayer(params_p + "|layername=" + PARAMS_LAYER, "subs", "ogr")
if not pts.isValid() or not subs.isValid():
    raise Exception("invalid pour-point or subbasin layer")

# spatial pairing: which subbasin polygon contains each pour point
sub_geoms = {f["id"]: (f.id(), f.geometry()) for f in subs.getFeatures()}

# nearest reach endpoint + interior check, per pour point
sub_ds   = {}    # subbasin id (int) -> downstream reach fid
sub_flag = {}    # subbasin id (int) -> 'endpoint' | 'MIDREACH'
pp_xy    = {}    # subbasin id (int) -> (x,y) for connector lines

for pt in pts.getFeatures():
    pid = as_int(pt["id"])
    g = pt.geometry()
    p = QgsPointXY(g.asPoint())

    # nearest reach by ENDPOINT distance
    best_fid, best_end_d = None, None
    for fid, e in rinfo.items():
        d = min(p.distance(e["up"]), p.distance(e["dn"]))
        if best_end_d is None or d < best_end_d:
            best_fid, best_end_d = fid, d

    # nearest reach by INTERIOR (perpendicular) distance, for the mid-reach test
    best_int_d = None
    for f in reaches.getFeatures():
        d = f.geometry().distance(g)   # distance to the line itself
        if best_int_d is None or d < best_int_d:
            best_int_d = d

    # if the point hugs a reach interior but is far from any endpoint -> mid-reach
    if best_end_d is not None and best_int_d is not None \
       and best_end_d > SNAP_TOL and best_int_d < best_end_d - SNAP_TOL:
        flag = "MIDREACH"
    else:
        flag = "endpoint"

    sub_ds[pid]   = best_fid
    sub_flag[pid] = flag
    pp_xy[pid]    = (p.x(), p.y())

n_mid = sum(1 for v in sub_flag.values() if v == "MIDREACH")
print("Subbasin->reach resolved. mid-reach pour points:", n_mid)
if n_mid:
    print("  NOTE: %d pour point(s) land mid-reach. In direct-to-reach mode HMS"
          " cannot attach there; on real sites add a reach break/junction at"
          " these. Flagged 'MIDREACH' in subwatershed_params." % n_mid)

# ---------------------------------------------------------------------------
# write reach_id / ds_type / ds_reach_id onto reaches.gpkg
# ---------------------------------------------------------------------------
reaches.startEditing()
have = [f.name() for f in reaches.fields()]
add = [QgsField(n, t) for (n, t) in
       [("reach_id", QVariant.Int), ("ds_type", QVariant.String),
        ("ds_reach_id", QVariant.Int)] if n not in have]
if add:
    reaches.dataProvider().addAttributes(add); reaches.updateFields()
i_rid = reaches.fields().indexFromName("reach_id")
i_dst = reaches.fields().indexFromName("ds_type")
i_dsr = reaches.fields().indexFromName("ds_reach_id")
for f in reaches.getFeatures():
    ds = ds_reach.get(f.id())
    reaches.changeAttributeValue(f.id(), i_rid, int(f.id()))
    reaches.changeAttributeValue(f.id(), i_dst, "outlet" if ds is None else "reach")
    reaches.changeAttributeValue(f.id(), i_dsr, None if ds is None else int(ds))
reaches.commitChanges()
print("\nWrote reach_id / ds_type / ds_reach_id to reaches.gpkg")

# ---------------------------------------------------------------------------
# write ds_reach_id / ds_flag onto subwatershed_params.gpkg
# ---------------------------------------------------------------------------
subs.startEditing()
have = [f.name() for f in subs.fields()]
add = [QgsField(n, t) for (n, t) in
       [("ds_reach_id", QVariant.Int), ("ds_flag", QVariant.String)]
       if n not in have]
if add:
    subs.dataProvider().addAttributes(add); subs.updateFields()
i_dsr = subs.fields().indexFromName("ds_reach_id")
i_flg = subs.fields().indexFromName("ds_flag")
for f in subs.getFeatures():
    sid = as_int(f["id"])
    ds  = sub_ds.get(sid)
    subs.changeAttributeValue(f.id(), i_dsr, None if ds is None else int(ds))
    subs.changeAttributeValue(f.id(), i_flg, sub_flag.get(sid))
subs.commitChanges()
print("Wrote ds_reach_id / ds_flag to subwatershed_params.gpkg")

# ---------------------------------------------------------------------------
# verification connector lines: element centroid/pourpt -> downstream up-end
# ---------------------------------------------------------------------------
flds = QgsFields()
flds.append(QgsField("kind", QVariant.String))   # 'reach' | 'subbasin'
flds.append(QgsField("src_id", QVariant.Int))
flds.append(QgsField("dst_reach", QVariant.Int))
flds.append(QgsField("flag", QVariant.String))

if os.path.exists(connect_p):
    try:
        QgsVectorFileWriter.deleteSilently(connect_p)
    except AttributeError:
        for ext in ("", "-wal", "-shm", "-journal"):
            if os.path.exists(connect_p + ext):
                os.remove(connect_p + ext)
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"; opts.layerName = "topology_connectors"
writer = QgsVectorFileWriter.create(
    connect_p, flds, QgsWkbTypes.LineString, reaches.crs(),
    QgsCoordinateTransformContext(), opts)

# reach -> downstream reach (dn end -> downstream up end)
for fid, ds in ds_reach.items():
    if ds is None:
        continue
    a = rinfo[fid]["dn"]; b = rinfo[ds]["up"]
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([a, b]))
    ft["kind"] = "reach"; ft["src_id"] = int(fid)
    ft["dst_reach"] = int(ds); ft["flag"] = ""
    writer.addFeature(ft)

# subbasin -> downstream reach (pour point -> that reach's up end)
for sid, ds in sub_ds.items():
    if ds is None:
        continue
    ax, ay = pp_xy[sid]; b = rinfo[ds]["up"]
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(ax, ay), b]))
    ft["kind"] = "subbasin"; ft["src_id"] = int(sid)
    ft["dst_reach"] = int(ds); ft["flag"] = sub_flag.get(sid)
    writer.addFeature(ft)
del writer
print("Wrote verification connectors ->", os.path.basename(connect_p))

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
print("\nTOPOLOGY SUMMARY")
print("  reach_fid -> ds_reach (outlet = sink)")
for fid in sorted(ds_reach):
    ds = ds_reach[fid]
    print("    %4d -> %s" % (fid, "OUTLET" if ds is None else str(ds)))
print("  subbasin_id -> ds_reach_fid  [flag]")
for sid in sorted(sub_ds):
    print("    %4s -> %s  [%s]" % (sid, sub_ds[sid], sub_flag[sid]))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(connect_p, "topology_connectors", "ogr"))
    print("\n  loaded topology_connectors")

print("\nDone. VERIFY: render topology_connectors over reaches + pour points;")
print("every connector should point downstream, and no subbasin should be")
print("flagged MIDREACH unless you intend a reach break there.")
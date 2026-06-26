# =============================================================================
# materialize_junctions.py   (QGIS Python Console)
#
# NHA WS3, HMS network step: create JUNCTION elements at true confluences and
# rewire reach downstream pointers through them. Run AFTER derive_topology.py.
#
# RULE
#   A junction is created where >= 3 reach-ends coincide within SNAP_TOL
#   (a hydrologic confluence: 2 reaches end here -- their DOWNSTREAM ends --
#   and 1 reach begins here -- its UPSTREAM end). End-to-end seams (1 down + 1
#   up = 2 ends) are NOT junctions; those stay direct reach -> reach.
#
# REWIRING
#   For each confluence:
#     - reaches whose DOWNSTREAM end is at the point  -> ds_type='junction',
#       ds_junction_id=<J>, ds_reach_id cleared.
#     - the reach whose UPSTREAM end is at the point is the junction's single
#       outflow: junction.ds_type='reach', junction.ds_reach_id=<that reach>.
#   Reaches not at any confluence keep their direct reach->reach pointer from
#   derive_topology.py. The outlet reach stays ds_type='outlet'.
#
#   Subbasins already drain direct to a reach (ds_reach_id). They are not
#   rewired here; a subbasin whose reach now feeds a junction still enters at
#   that reach's upstream end, which is the junction -- consistent.
#
# OUTPUTS (in <SITE>/outputs/)
#   reaches.gpkg        + ds_junction_id (Int); ds_type may become 'junction'
#   junctions.gpkg      NEW point layer: junction_id, x, y, ds_type,
#                       ds_reach_id, n_ends, n_in
#   topology_connectors.gpkg  rewritten to route through junctions (verify!)
#
# VERIFY BEFORE SEALING: render junctions + connectors over reaches. Every
# confluence should carry exactly one junction with >=2 reaches in and 1 out.
# A confluence with NO junction means snapping left <3 ends coincident -- relax
# the rule to the downstream-end test if that appears.
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
PARAMS_NAME  = "subwatershed_params.gpkg"
PARAMS_LAYER = "subwatershed_params"

PIXEL_M = 9.336
SNAP_PX = 1.5
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
junc_p    = os.path.join(OUT_DIR, "junctions.gpkg")
connect_p = os.path.join(OUT_DIR, "topology_connectors.gpkg")
SNAP_TOL  = SNAP_PX * PIXEL_M

if not os.path.isfile(reaches_p):
    raise Exception("not found: " + reaches_p)

reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer")
if "ds_reach_id" not in [f.name() for f in reaches.fields()]:
    raise Exception("run derive_topology.py first (no ds_reach_id field)")

def line_endpoints(geom):
    if geom.isMultipart():
        parts = geom.asMultiPolyline()
        pts = [p for part in parts for p in part]
    else:
        pts = geom.asPolyline()
    return QgsPointXY(pts[0]), QgsPointXY(pts[-1])

# build oriented endpoints (same elevation-orientation as derive_topology)
rinfo = {}
for f in reaches.getFeatures():
    fp, lp = line_endpoints(f.geometry())
    zu, zd = f["z_up_m"], f["z_dn_m"]
    if zu is not None and zd is not None and zd > zu:
        up_pt, dn_pt = lp, fp
    else:
        up_pt, dn_pt = fp, lp
    rinfo[f.id()] = {"up": up_pt, "dn": dn_pt}

print("Reaches:", len(rinfo), " snap tol: %.2f m" % SNAP_TOL)

# ---------------------------------------------------------------------------
# collect all reach-ends as (fid, role, point); cluster by coincidence
# ---------------------------------------------------------------------------
ends = []
for fid, e in rinfo.items():
    ends.append((fid, "dn", e["dn"]))
    ends.append((fid, "up", e["up"]))

clusters = []   # each: {"pt": QgsPointXY (mean), "members": [(fid,role),...]}
for fid, role, p in ends:
    placed = False
    for c in clusters:
        if c["pt"].distance(p) <= SNAP_TOL:
            c["members"].append((fid, role))
            # update cluster centroid (running mean)
            n = len(c["members"])
            c["pt"] = QgsPointXY(
                (c["pt"].x() * (n - 1) + p.x()) / n,
                (c["pt"].y() * (n - 1) + p.y()) / n)
            placed = True
            break
    if not placed:
        clusters.append({"pt": QgsPointXY(p.x(), p.y()),
                         "members": [(fid, role)]})

# confluence = cluster with >= 3 ends coincident
confluences = [c for c in clusters if len(c["members"]) >= 3]
print("Confluence clusters (>=3 ends):", len(confluences))

# ---------------------------------------------------------------------------
# build junctions and rewiring maps
# ---------------------------------------------------------------------------
# reach fid -> ('junction', J)   for reaches whose DN end is at a confluence
# junction J -> outflow reach fid (the reach whose UP end is at the confluence)
reach_to_junc = {}
junc_outflow  = {}
junctions = []   # (J, pt, n_ends, n_in)

for j, c in enumerate(confluences, start=1):
    ins  = [fid for (fid, role) in c["members"] if role == "dn"]
    outs = [fid for (fid, role) in c["members"] if role == "up"]
    junctions.append((j, c["pt"], len(c["members"]), len(ins)))
    for fid in ins:
        reach_to_junc[fid] = j
    # a clean confluence has exactly one outflow; if >1, flag it
    if len(outs) == 1:
        junc_outflow[j] = outs[0]
    elif len(outs) == 0:
        junc_outflow[j] = None     # confluence at the outlet (no downstream reach)
        print("  J%d: confluence with no outflow reach (outlet confluence)" % j)
    else:
        junc_outflow[j] = outs[0]  # pick first; flag
        print("  WARNING J%d: %d outflow reaches at one confluence "
              "(expected 1). Using fid %d; check geometry." % (j, len(outs), outs[0]))

# ---------------------------------------------------------------------------
# write ds_junction_id onto reaches; flip ds_type to 'junction' where rewired
# ---------------------------------------------------------------------------
reaches.startEditing()
if "ds_junction_id" not in [f.name() for f in reaches.fields()]:
    reaches.dataProvider().addAttributes([QgsField("ds_junction_id", QVariant.Int)])
    reaches.updateFields()
i_dst = reaches.fields().indexFromName("ds_type")
i_dsr = reaches.fields().indexFromName("ds_reach_id")
i_dsj = reaches.fields().indexFromName("ds_junction_id")
for f in reaches.getFeatures():
    fid = f.id()
    if fid in reach_to_junc:
        j = reach_to_junc[fid]
        reaches.changeAttributeValue(fid, i_dst, "junction")
        reaches.changeAttributeValue(fid, i_dsr, None)
        reaches.changeAttributeValue(fid, i_dsj, int(j))
    else:
        # leave derive_topology pointer (reach or outlet); clear junction id
        reaches.changeAttributeValue(fid, i_dsj, None)
reaches.commitChanges()
print("Rewired %d reach(es) to junctions." % len(reach_to_junc))

# ---------------------------------------------------------------------------
# write junctions.gpkg
# ---------------------------------------------------------------------------
flds = QgsFields()
flds.append(QgsField("junction_id", QVariant.Int))
flds.append(QgsField("x", QVariant.Double))
flds.append(QgsField("y", QVariant.Double))
flds.append(QgsField("ds_type", QVariant.String))     # 'reach' | 'outlet'
flds.append(QgsField("ds_reach_id", QVariant.Int))
flds.append(QgsField("n_ends", QVariant.Int))
flds.append(QgsField("n_in", QVariant.Int))

# Release any project lock on this GPKG (Windows) before overwriting.
_proj0 = QgsProject.instance()
for _lyr in list(_proj0.mapLayers().values()):
    try:
        _src = _lyr.source().split("|", 1)[0]
    except Exception:
        _src = ""
    if os.path.normcase(os.path.abspath(_src)) == \
       os.path.normcase(os.path.abspath(junc_p)):
        _proj0.removeMapLayer(_lyr.id())

opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"; opts.layerName = "junctions"
opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
w = QgsVectorFileWriter.create(junc_p, flds, QgsWkbTypes.Point, reaches.crs(),
                               QgsCoordinateTransformContext(), opts)
for (j, pt, n_ends, n_in) in junctions:
    out = junc_outflow.get(j)
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPointXY(pt))
    ft["junction_id"] = int(j); ft["x"] = pt.x(); ft["y"] = pt.y()
    ft["ds_type"] = "outlet" if out is None else "reach"
    ft["ds_reach_id"] = None if out is None else int(out)
    ft["n_ends"] = int(n_ends); ft["n_in"] = int(n_in)
    w.addFeature(ft)
del w
print("Wrote", os.path.basename(junc_p), "with", len(junctions), "junction(s)")

# ---------------------------------------------------------------------------
# rewrite verification connectors to route THROUGH junctions
# ---------------------------------------------------------------------------
cflds = QgsFields()
cflds.append(QgsField("kind", QVariant.String))   # reach_in | junc_out | reach_direct
cflds.append(QgsField("src", QVariant.Int))
cflds.append(QgsField("dst", QVariant.Int))
# Drop any project layer that has this GPKG open, so Windows releases the
# file lock before we overwrite it. (On Windows os.remove() on a GPKG still
# loaded in the project raises WinError 32; CreateOrOverwriteFile then handles
# the replace without a manual unlink.)
_proj = QgsProject.instance()
for _lyr in list(_proj.mapLayers().values()):
    try:
        _src = _lyr.source().split("|", 1)[0]
    except Exception:
        _src = ""
    if os.path.normcase(os.path.abspath(_src)) == \
       os.path.normcase(os.path.abspath(connect_p)):
        _proj.removeMapLayer(_lyr.id())

opts2 = QgsVectorFileWriter.SaveVectorOptions()
opts2.driverName = "GPKG"; opts2.layerName = "topology_connectors"
opts2.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
cw = QgsVectorFileWriter.create(connect_p, cflds, QgsWkbTypes.LineString,
                                reaches.crs(), QgsCoordinateTransformContext(), opts2)
jpt = {j: pt for (j, pt, _, _) in junctions}
# reach -> junction (dn end to junction point)
for fid, j in reach_to_junc.items():
    ft = QgsFeature(cflds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([rinfo[fid]["dn"], jpt[j]]))
    ft["kind"] = "reach_in"; ft["src"] = int(fid); ft["dst"] = int(j)
    cw.addFeature(ft)
# junction -> outflow reach (junction point to that reach up end)
for j, out in junc_outflow.items():
    if out is None:
        continue
    ft = QgsFeature(cflds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([jpt[j], rinfo[out]["up"]]))
    ft["kind"] = "junc_out"; ft["src"] = int(j); ft["dst"] = int(out)
    cw.addFeature(ft)
# direct reach->reach for reaches NOT feeding a junction (seams)
for f in reaches.getFeatures():
    fid = f.id()
    if fid in reach_to_junc:
        continue
    if f["ds_type"] == "reach" and f["ds_reach_id"] is not None:
        ds = int(f["ds_reach_id"])
        if ds in rinfo:
            ft = QgsFeature(cflds)
            ft.setGeometry(QgsGeometry.fromPolylineXY([rinfo[fid]["dn"], rinfo[ds]["up"]]))
            ft["kind"] = "reach_direct"; ft["src"] = int(fid); ft["dst"] = ds
            cw.addFeature(ft)
del cw
print("Rewrote", os.path.basename(connect_p), "through junctions")

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
print("\nJUNCTION SUMMARY")
for (j, pt, n_ends, n_in) in junctions:
    out = junc_outflow.get(j)
    print("  J%d  ends=%d  in=%d  out=%s  @ (%.1f, %.1f)" %
          (j, n_ends, n_in, "OUTLET" if out is None else str(out), pt.x(), pt.y()))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(QgsVectorLayer(junc_p, "junctions", "ogr"))
    QgsProject.instance().addMapLayer(QgsVectorLayer(connect_p, "topology_connectors", "ogr"))

print("\nDone. VERIFY: junctions sit on every confluence; each has >=2 reaches")
print("in and exactly 1 out (or OUTLET). If a visible confluence has no junction,")
print("snapping left <3 ends coincident -- tell me and we relax the rule.")

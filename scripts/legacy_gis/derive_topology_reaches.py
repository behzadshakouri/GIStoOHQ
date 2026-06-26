# =============================================================================
# derive_topology_reaches.py   (QGIS Python Console)
#
# PHASE 1 half of derive_topology.py: derive REACH -> downstream REACH
# connectivity from geometry ONLY. No pour points, no subbasins -- this runs on
# the whole-watershed reach network before the operator places interior pour
# points, so materialize_junctions.py (which needs ds_reach_id) can find
# confluences.
#
# Each reach's downstream end (lower-elevation endpoint, from z_up_m/z_dn_m)
# is matched to another reach's UPSTREAM end within SNAP_PX pixels. The reach
# whose downstream end matches nothing is the OUTLET (sink).
#
# OUTPUTS (in <SITE>/outputs/)
#   reaches.gpkg              + reach_id, ds_type ('reach'|'outlet'), ds_reach_id
#   topology_connectors.gpkg  reach->reach verification lines (subbasin
#                             connectors are added later by the phase-2 script)
#
# The subbasin->reach wiring (ds_reach_id / ds_flag on subwatershed_params.gpkg)
# is done in phase 2 by derive_topology_subbasins.py, AFTER pour points exist.
#
# VERIFY: render topology_connectors over reaches; every arrow points downstream
# and exactly one reach is the OUTLET.
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

REACHES_NAME = "reaches.gpkg"
PIXEL_M = 9.336          # for snap tolerance
SNAP_PX = 1.5            # endpoint-coincidence tolerance, in pixels
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")
reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
connect_p = os.path.join(OUT_DIR, "topology_connectors.gpkg")
SNAP_TOL  = SNAP_PX * PIXEL_M

if not os.path.isfile(reaches_p):
    raise Exception("not found: " + reaches_p)

print("Reaches  :", reaches_p)
print("Snap tol : %.2f m (%.1f px)" % (SNAP_TOL, SNAP_PX))

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

rinfo = {}
for f in reaches.getFeatures():
    fp, lp = line_endpoints(f.geometry())
    zu, zd = f["z_up_m"], f["z_dn_m"]
    if zu is not None and zd is not None and zd > zu:
        up_pt, dn_pt = lp, fp        # first vertex is actually lower -> swap
    else:
        up_pt, dn_pt = fp, lp
    rinfo[f.id()] = {"up": up_pt, "dn": dn_pt}

print("\nReaches loaded:", len(rinfo))

# ---------------------------------------------------------------------------
# reach -> downstream reach: match each reach's DN end to another reach UP end
# ---------------------------------------------------------------------------
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
# verification connector lines: reach dn end -> downstream reach up end
# (subbasin connectors are added by the phase-2 subbasin-topology script)
# ---------------------------------------------------------------------------
flds = QgsFields()
flds.append(QgsField("kind", QVariant.String))
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
for fid, ds in ds_reach.items():
    if ds is None:
        continue
    a = rinfo[fid]["dn"]; b = rinfo[ds]["up"]
    ft = QgsFeature(flds)
    ft.setGeometry(QgsGeometry.fromPolylineXY([a, b]))
    ft["kind"] = "reach"; ft["src_id"] = int(fid)
    ft["dst_reach"] = int(ds); ft["flag"] = ""
    writer.addFeature(ft)
del writer
print("Wrote reach->reach connectors ->", os.path.basename(connect_p))

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
print("\nREACH TOPOLOGY SUMMARY")
print("  reach_fid -> ds_reach (outlet = sink)")
for fid in sorted(ds_reach):
    ds = ds_reach[fid]
    print("    %4d -> %s" % (fid, "OUTLET" if ds is None else str(ds)))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(connect_p, "topology_connectors", "ogr"))
    print("\n  loaded topology_connectors")

print("\nDone (phase 1 reach topology). Next: materialize_junctions.py to place")
print("junctions at confluences, then place pour points by hand.")

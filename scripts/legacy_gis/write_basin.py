# =============================================================================
# write_basin.py (QGIS Python Console)
#
# NHA WS3 -- generate a HEC-HMS .basin file from the delineation + topology
# products. Emits byte-compatible blocks for HMS 4.13 (grammar matched to a
# reference Basin_1.basin saved from this HMS install).
#
# READS (in <SITE>/outputs/)
#   subwatershed_params.gpkg : id, area_km2, CN, lag_min, centroid_x, centroid_y
#                              ds_kind ('junction'|'reach'), ds_junction_id,
#                              ds_reach_id (subbasin drains to its junction;
#                              reach is the mid-reach fallback)
#                              OPTIONAL overrides: loss_method, transform_method
#   reaches.gpkg             : reach_id, ds_type, ds_reach_id, ds_junction_id,
#                              length_m, slope_mm, base_w_m, side_z, manning_n,
#                              z_dn_m
#                              OPTIONAL override: route_method
#   junctions.gpkg           : junction_id, x, y, ds_type, ds_reach_id
#
# WRITES
#   <SITE>/outputs/<BASIN_NAME>.basin
#
# UNITS: U.S. Customary (English). area km2->mi2, length m->ft, width m->ft,
#        slope is ratio (ft/ft), lag in minutes, elevations m->ft.
#
# METHODS (selectable; per-element override columns honored if present)
#   Subbasin loss      : SCS Curve Number (default)
#   Subbasin transform : SCS Unit Hydrograph (Lag) (default)
#   Reach routing      : Muskingum Cunge / Trapezoid (default)
#   Alternatives understood for overrides:
#     loss:      "SCS" | "Initial+Constant"
#     transform: "SCS" | "Clark"
#     route:     "Muskingum Cunge" | "Lag"
#
# LAG NOTE: lag_min = 0.6*Tc(NRCS), consistent with the SCS UH transform.
#           The Tc METHOD itself must be confirmed against RFP #660 before sealing.
#
# OUTLET: the reach with ds_type='outlet' drains to a Sink element.
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================

import os
from datetime import datetime
from qgis.core import (
    QgsVectorLayer, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject, QgsPointXY
)

# --- safe field coercion ---------------------------------------------------

def num(v, default=None):
    """Field value -> float, or `default` if None/NULL/non-numeric."""
    if v is None:
        return default
    try:
        if hasattr(v, "isNull") and v.isNull():
            return default
    except Exception:
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def iid(v):
    """Field value -> int, or None if None/NULL/non-integer."""
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

# ---------------------------------------------------------------------------
# --- settings --------------------------------------------------------------

try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

import os as _os
BASIN_NAME = _os.path.basename(_os.path.normpath(SITE_DIR))
HMS_NAME   = BASIN_NAME.replace("-", "_")   # HMS forbids hyphens in model names

# defaults (overridable per element via optional gpkg columns)
DEF_LOSS      = "SCS"
DEF_TRANSFORM = "SCS"
DEF_ROUTE     = "Muskingum Cunge"

# fixed-form values for blocks (match reference Basin_1.basin)
MC_INDEX_FLOW  = 1
MC_DEPTH_ITERS = 20
MC_STEP_ITERS  = 30
LAG_FLOOR_MIN  = 6.0   # do not write a lag below this (matches Tc floor)

# ---------------------------------------------------------------------------

site_path  = os.path.join(ROOT, SITE_DIR)
OUT_DIR    = os.path.join(site_path, "outputs")
params_p   = os.path.join(OUT_DIR, "subwatershed_params.gpkg")
reaches_p  = os.path.join(OUT_DIR, "reaches.gpkg")
junc_p     = os.path.join(OUT_DIR, "junctions.gpkg")

try:
    HMS_DIR
except NameError:
    HMS_DIR = os.path.join(ROOT, "WS3_HMS")

HMS_PROJ_DIR = os.path.join(HMS_DIR, HMS_NAME)
os.makedirs(HMS_PROJ_DIR, exist_ok=True)
basin_out  = os.path.join(HMS_PROJ_DIR, HMS_NAME + ".basin")

M2_TO_MI2 = 1.0 / 2_589_988.110336
M_TO_FT   = 3.280839895
CRLF      = "\r\n"

def blk(lines): return CRLF.join(lines) + CRLF

# --- load layers -----------------------------------------------------------

subs    = QgsVectorLayer(params_p + "|layername=subwatershed_params", "subs", "ogr")
reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
juncs   = QgsVectorLayer(junc_p, "junctions", "ogr")

for lyr, nm in ((subs, "subbasins"), (reaches, "reaches"), (juncs, "junctions")):
    if not lyr.isValid():
        raise Exception("invalid layer: " + nm)

src_crs = reaches.crs()
to_wgs  = QgsCoordinateTransform(src_crs,
               QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())

def field_names(lyr): return [f.name() for f in lyr.fields()]
sub_flds = field_names(subs)
rch_flds = field_names(reaches)

def latlon(x, y):
    p = to_wgs.transform(QgsPointXY(x, y))
    return p.y(), p.x()   # lat, lon

now  = datetime.now()
DATE = now.strftime("%d %B %Y")
TIME = now.strftime("%H:%M:%S")

def hdr(name_line):
    return [name_line,
            "     Last Modified Date: " + DATE,
            "     Last Modified Time: " + TIME]

# --- element name maps -----------------------------------------------------

def sub_name(i): return "Subbasin_%s" % i
def rch_name(i): return "Reach_%s" % i
def jct_name(i): return "Junction_%s" % i

SINK_NAME = "Outlet"

# ==========================================================================
# TOPOLOGY
# ==========================================================================

topo_p = os.path.join(OUT_DIR, "topology.gpkg")
if not os.path.isfile(topo_p):
    raise Exception("topology.gpkg not found -- run build_topology.py before write_basin.py")

topo = QgsVectorLayer(topo_p + "|layername=topology", "topology", "ogr")
if not topo.isValid():
    raise Exception("invalid topology layer: " + topo_p)

DS_NAME   = {}
IN_MODEL  = set()
TOPO_NOTE = {}

for f in topo.getFeatures():
    nm = f["name"]
    IN_MODEL.add(nm)
    DS_NAME[nm] = f["ds_name"]
    if f["note"]:
        TOPO_NOTE[nm] = f["note"]

def ds_of(name):
    return DS_NAME.get(name, SINK_NAME)

SUB_IN = {n for n in IN_MODEL if n.startswith("Subbasin_")}
RCH_IN = {n for n in IN_MODEL if n.startswith("Reach_")}
JCT_IN = {n for n in IN_MODEL if n.startswith("Junction_")}

print("Topology loaded: %d subbasins, %d reaches, %d junctions"
      % (len(SUB_IN), len(RCH_IN), len(JCT_IN)))

# ==========================================================================
# compose blocks
# ==========================================================================

out = []

# ---- file header block ----
out.append(blk(
    ["Basin: " + HMS_NAME] + hdr("")[1:] + [
        "     Version: 4.13",
        "     Filepath Separator: \\",
        "     Unit System: English",
        "     Missing Flow To Zero: No",
        "     Enable Flow Ratio: No",
        "     Compute Local Flow At Junctions: No",
        "     Unregulated Output Required: No",
        "",
        "     Enable Sediment Routing: No",
        "End:"]))

# ---- subbasins ----
for f in subs.getFeatures():
    sid = int(f["id"])
    if sub_name(sid) not in SUB_IN:
        continue

    area_mi2 = num(f["area_km2"], 0.0) * 1e6 * M2_TO_MI2
    cx, cy   = num(f["centroid_x"], 0.0), num(f["centroid_y"], 0.0)
    lat, lon = latlon(cx, cy)

    loss  = (f["loss_method"]      if "loss_method"      in sub_flds and f["loss_method"]      else DEF_LOSS)
    trans = (f["transform_method"] if "transform_method" in sub_flds and f["transform_method"] else DEF_TRANSFORM)

    ds_name = ds_of(sub_name(sid))

    lines = hdr("Subbasin: " + sub_name(sid))
    lines += [
        "     Latitude Degrees: %.14f"  % lat,
        "     Longitude Degrees: %.14f" % lon,
        "     Canvas X: %.6f"           % cx,
        "     Canvas Y: %.6f"           % cy,
        "     Area: %.4f"               % area_mi2,
        "     Downstream: "             + ds_name,
        "",
        "     Discretization: None",
        "     File: ",
        "",
        "     Canopy: None",
        "     Allow Simultaneous Precip Et: No",
        "     Plant Uptake Method: None",
        "",
        "     Surface: None",
        ""]

    if loss == "SCS":
        cn = num(f["CN"])
        lines += [
            "     LossRate: SCS",
            "     Percent Impervious Area: 0.0",
            "     Curve Number: %s" % (int(round(cn)) if cn is not None else 0),
            "     Initial Abstraction: 0.2",
            ""]
    else:
        lines += [
            "     LossRate: Initial+Constant",
            "     Percent Impervious Area: 0.0",
            ""]

    if trans == "SCS":
        lag = num(f["lag_min"], 0.0)
        if lag < LAG_FLOOR_MIN:
            lag = LAG_FLOOR_MIN
        lines += [
            "     Transform: SCS",
            "     Lag: %g" % lag,
            "     Unitgraph Type: STANDARD",
            ""]
    else:
        lines += [
            "     Transform: Clark",
            "     Clark Method: Specified",
            "     Time Area Method: Default",
            ""]

    lines += [
        "     Baseflow: None",
        "End:"]

    out.append(blk(lines))

# ---- reaches ----

def line_ends(geom):
    if geom.isMultipart():
        pts = [p for part in geom.asMultiPolyline() for p in part]
    else:
        pts = geom.asPolyline()
    return (pts[0].x(), pts[0].y()), (pts[-1].x(), pts[-1].y())

for f in reaches.getFeatures():
    rid = iid(f["reach_id"])
    if rid is None:
        rid = int(f.id())
    if rch_name(rid) not in RCH_IN:
        continue

    (x0, y0), (x1, y1) = line_ends(f.geometry())
    zu, zd = num(f["z_up_m"]), num(f["z_dn_m"])
    if zu is not None and zd is not None and zd > zu:
        up_xy, dn_xy = (x1, y1), (x0, y0)
    else:
        up_xy, dn_xy = (x0, y0), (x1, y1)

    route   = (f["route_method"] if "route_method" in rch_flds and f["route_method"] else DEF_ROUTE)
    ds_name = ds_of(rch_name(rid))

    lines = hdr("Reach: " + rch_name(rid))
    lines += [
        "     Canvas X: %.6f"      % dn_xy[0],
        "     Canvas Y: %.6f"      % dn_xy[1],
        "     From Canvas X: %.6f" % up_xy[0],
        "     From Canvas Y: %.6f" % up_xy[1],
        "     Downstream: "        + ds_name,
        ""]

    if route == "Muskingum Cunge":
        length_ft = num(f["length_m"], 0.0) * M_TO_FT
        slope     = num(f["slope_mm"], 0.0005)
        n         = num(f["manning_n"], 0.035)
        bw_ft     = num(f["base_w_m"], 0.0) * M_TO_FT
        z         = num(f["side_z"], 2.0)
        zdn       = num(f["z_dn_m"])
        invert_ft = (zdn * M_TO_FT) if zdn is not None else 0.0

        lines += [
            "     Route: Muskingum Cunge",
            "     Channel: Trapezoid",
            "     Length: %g"                      % round(length_ft, 2),
            "     Energy Slope: %g"                % slope,
            "     Mannings n: %g"                  % n,
            "     Bottom Width: %g"                % round(bw_ft, 2),
            "     Side Slope: %g"                  % z,
            "     Initial Variable: Combined Inflow",
            "     Space-Time Method: Automatic DX and DT",
            "     Index Parameter Type: Index Flow",
            "     Index Flow: %g"                  % MC_INDEX_FLOW,
            "     Invert Elevation: %g"            % round(invert_ft, 2),
            "     Maximum Depth Iterations: %d"    % MC_DEPTH_ITERS,
            "     Maximum Route Step Iterations: %d" % MC_STEP_ITERS,
            "     Channel Loss: None",
            "End:"]
    else:
        lines += [
            "     Route: Lag",
            "     Initial Variable: Combined Inflow",
            "     Channel Loss: None",
            "End:"]

    out.append(blk(lines))

# ---- junctions ----

sink_needed = False

for f in juncs.getFeatures():
    jid = iid(f["junction_id"])
    if jct_name(jid) not in JCT_IN:
        continue

    x, y    = num(f["x"], 0.0), num(f["y"], 0.0)
    ds_name = ds_of(jct_name(jid))

    if ds_name == SINK_NAME:
        sink_needed = True

    lines = hdr("Junction: " + jct_name(jid))
    lines += [
        "     Canvas X: %.6f" % x,
        "     Canvas Y: %.6f" % y,
        "     Downstream: "   + ds_name,
        "End:"]

    out.append(blk(lines))

if SINK_NAME in DS_NAME.values():
    sink_needed = True

outlet_reach = None
for f in reaches.getFeatures():
    if f["ds_type"] == "outlet":
        outlet_reach = f

# ---- sink ----

if sink_needed:
    sx, sy = 0.0, 0.0
    if outlet_reach is not None:
        (x0, y0), (x1, y1) = line_ends(outlet_reach.geometry())
        zu, zd = num(outlet_reach["z_up_m"]), num(outlet_reach["z_dn_m"])
        sx, sy = (x0, y0) if (zu is not None and zd is not None and zd > zu) else (x1, y1)

    lines = hdr("Sink: " + SINK_NAME)
    lines += [
        "     Canvas X: %.6f" % sx,
        "     Canvas Y: %.6f" % sy,
        "End:"]

    out.append(blk(lines))

# ---- file footer (Basin Schematic Properties) ----
# Note: Hms Schematic, Map Visible, Gridlines Visible, Flow Direction Visible
# were removed -- these fields are deprecated in HMS 4.13 and cause warnings.
out.append(blk([
    "Basin Schematic Properties:",
    "     Last View N: 3993169.516",
    "     Last View S: 3987721.268",
    "     Last View W: 596457.304",
    "     Last View E: 600240.677",
    "     Maximum View N: 3993169.516",
    "     Maximum View S: 3987721.268",
    "     Maximum View W: 596457.304",
    "     Maximum View E: 600240.677",
    "     Map: None",
    "End:"]))

# --- write -----------------------------------------------------------------

with open(basin_out, "w", newline="") as fh:
    fh.write(CRLF.join(b.rstrip(CRLF) for b in out) + CRLF)

nb_sub = len(SUB_IN); nb_rch = len(RCH_IN); nb_jct = len(JCT_IN)
print("Wrote", basin_out)
print("  Subbasins: %d | Reaches: %d | Junctions: %d | Sink: %s"
      % (nb_sub, nb_rch, nb_jct, "yes" if sink_needed else "no"))
print("  Defaults -> loss:%s  transform:%s  route:%s" % (DEF_LOSS, DEF_TRANSFORM, DEF_ROUTE))
print("VERIFY: open in HMS. Confirm it loads, the network wires to a single")
print("Sink, and spot-check one subbasin (CN, Lag) and one reach (Length, n).")
print("PRE-SEAL: confirm the Tc/lag method against RFP #660.")

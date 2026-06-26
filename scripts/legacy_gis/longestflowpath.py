# =============================================================================
# longest_flow_path.py   (QGIS Python Console)
#
# Adds the LONGEST FLOW PATH and its derived parameters to each subwatershed in
# subwatershed_params.gpkg, and writes a flow-path line layer for inspection.
#
# For each subwatershed it finds the hydraulically most distant cell and traces
# downstream (along the GRASS r.watershed flow-direction grid) to that
# subwatershed's outlet, giving the longest flow path. From that path:
#   flow_len_ft   path length (feet)              -- the L in every Tc equation
#   elev_max_ft   elevation at the distant point  (feet)
#   elev_min_ft   elevation at the outlet         (feet)
#   slope_lfp     straight rise/run over the path (ft/ft)  -- 2nd slope definition
#   slope_1085    drop between 10% and 85% points (ft/ft)  -- damps end anomalies
#
# Grids (note: NOT the 30 m CN grid -- these are the native ~9.34 m delineation
# grid, which is finer and better for tracing; lengths are in meters internally
# then converted to feet):
#   flow_dir.tif    GRASS r.watershed direction, 1-8 CCW from east, |v| used,
#                   negative = drains off-region. ~9.34 m, UTM.
#   dem_carved.tif  same grid; elevations sampled here (the surface routing used).
# Outlets: pour_points_snapped.gpkg, each point matched to the subwatershed that
#          contains it. Masking: each trace is confined to its subwatershed polygon.
#
# Appends columns to subwatershed_params.gpkg IN PLACE (CN, slope_pct preserved)
# and writes longest_flow_paths.gpkg (one line per subwatershed).
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os, math, struct
from osgeo import gdal
from qgis.core import (QgsProject, QgsVectorLayer, QgsField, QgsFields,
                       QgsFeature, QgsGeometry, QgsPointXY, QgsVectorFileWriter,
                       QgsCoordinateTransformContext, QgsWkbTypes)
from qgis.PyQt.QtCore import QVariant
gdal.UseExceptions()

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

FLOWDIR_NAME = "flow_dir.tif"
DEM_NAME     = "dem_carved.tif"
SUBWS_NAME   = "subwatersheds.gpkg";          SUBWS_LAYER  = "subwatersheds"
POUR_NAME    = "pour_points_snapped.gpkg"     # snapped outlets
PARAMS_NAME  = "subwatershed_params.gpkg";    PARAMS_LAYER = "subwatershed_params"
LFP_NAME     = "longest_flow_paths.gpkg";     LFP_LAYER    = "longest_flow_paths"

M_TO_FT = 3.280839895
ADD_TO_PROJECT = True
# ---------------------------------------------------------------------------

# GRASS r.watershed direction -> (drow, dcol). Row increases southward.
# 1=NE,2=N,3=NW,4=W,5=SW,6=S,7=SE,8=E  (counterclockwise from east)
GRASS_OFF = {1:(-1, 1), 2:(-1, 0), 3:(-1,-1), 4:(0,-1),
             5:( 1,-1), 6:( 1, 0), 7:( 1, 1), 8:(0, 1)}

site = os.path.join(ROOT, SITE_DIR); OUT = os.path.join(site, "outputs")
fd_path   = os.path.join(OUT, FLOWDIR_NAME)
dem_path  = os.path.join(OUT, DEM_NAME)
subws_path= os.path.join(OUT, SUBWS_NAME)
pour_path = os.path.join(OUT, POUR_NAME)
params    = os.path.join(OUT, PARAMS_NAME)
lfp_path  = os.path.join(OUT, LFP_NAME)

print("Site :", site)
for p in (fd_path, dem_path, subws_path, pour_path, params):
    if not os.path.isfile(p):
        raise Exception("not found: " + p)

# --- read flow_dir + dem into row lists, capture geotransform --------------
fdds = gdal.Open(fd_path); fdb = fdds.GetRasterBand(1)
gt = fdds.GetGeoTransform(); proj = fdds.GetProjection()
NX, NY = fdds.RasterXSize, fdds.RasterYSize
px, py = gt[1], -gt[5]                      # pixel sizes (m), py>0
FD = [list(struct.unpack("%dh" % NX, fdb.ReadRaster(0, r, NX, 1))) for r in range(NY)]
demds = gdal.Open(dem_path); demb = demds.GetRasterBand(1)
DEM = [list(struct.unpack("%df" % NX, demb.ReadRaster(0, r, NX, 1))) for r in range(NY)]
print("Grid : %d x %d  pixel %.3f m" % (NX, NY, px))

ortho = (px + py) / 2.0
diag  = ortho * math.sqrt(2)

def to_rc(x, y):
    c = int((x - gt[0]) / gt[1]); r = int((y - gt[3]) / gt[5])
    return r, c

def to_xy(r, c):                            # cell center
    x = gt[0] + (c + 0.5) * gt[1]; y = gt[3] + (r + 0.5) * gt[5]
    return x, y

def trace(r, c):
    """downstream path (list of (r,c)) following GRASS flow dir, until edge/sink."""
    path = [(r, c)]; seen = set(); steps = 0
    while True:
        if (r, c) in seen:                  # guard against loops
            break
        seen.add((r, c))
        code = abs(FD[r][c])
        if code not in GRASS_OFF:
            break
        dr, dc = GRASS_OFF[code]; nr, nc = r + dr, c + dc
        if not (0 <= nr < NY and 0 <= nc < NX):
            break
        path.append((nr, nc)); r, c = nr, nc; steps += 1
        if steps > NX * NY:
            break
    return path

def cumdist(path):
    d = [0.0]
    for (r0, c0), (r1, c1) in zip(path, path[1:]):
        d.append(d[-1] + (diag if (r0 != r1 and c0 != c1) else ortho))
    return d

def slope_1085(path, dd):
    L = dd[-1]
    if L <= 0:
        return 0.0
    def elev_at(frac):
        t = frac * L
        for i in range(len(dd) - 1):
            if dd[i] <= t <= dd[i + 1]:
                r, c = path[i]; return DEM[r][c]
        r, c = path[-1]; return DEM[r][c]
    return abs(elev_at(0.85) - elev_at(0.10)) / (0.75 * L)

# --- load subwatersheds + snapped pour points ------------------------------
subws = QgsVectorLayer(subws_path + "|layername=" + SUBWS_LAYER, "sw", "ogr")
if not subws.isValid():
    subws = QgsVectorLayer(subws_path, "sw", "ogr")
pour = QgsVectorLayer(pour_path, "pp", "ogr")
if not (subws.isValid() and pour.isValid()):
    raise Exception("could not open subwatersheds or pour points")

pour_pts = [f.geometry().asPoint() for f in pour.getFeatures()
            if not f.geometry().isEmpty()]

# --- per-subwatershed longest flow path ------------------------------------
results = {}            # id -> dict of params
lines   = {}            # id -> list of (x,y) for the path line

for sw in subws.getFeatures():
    sid = int(sw["id"])
    geom = sw.geometry()
    # outlet = the snapped pour point inside this subwatershed
    outlet = None
    for p in pour_pts:
        if geom.contains(QgsGeometry.fromPointXY(p)):
            outlet = p; break
    if outlet is None:                       # fallback: nearest pour point
        outlet = min(pour_pts, key=lambda p: geom.distance(QgsGeometry.fromPointXY(p)))
    o_r, o_c = to_rc(outlet.x(), outlet.y())

    # cells of this subwatershed (rasterize the polygon footprint by point test
    # is slow; instead bound by the polygon bbox then test containment)
    bb = geom.boundingBox()
    r0, c0 = to_rc(bb.xMinimum(), bb.yMaximum())
    r1, c1 = to_rc(bb.xMaximum(), bb.yMinimum())
    r0, r1 = max(0, min(r0, r1)), min(NY - 1, max(r0, r1))
    c0, c1 = max(0, min(c0, c1)), min(NX - 1, max(c0, c1))

    best_path = None; best_len = -1.0
    for rr in range(r0, r1 + 1):
        for cc in range(c0, c1 + 1):
            x, y = to_xy(rr, cc)
            if not geom.contains(QgsGeometry.fromPointXY(QgsPointXY(x, y))):
                continue
            path = trace(rr, cc)
            # keep only the portion up to the outlet if the outlet is on the path
            if (o_r, o_c) in path:
                path = path[:path.index((o_r, o_c)) + 1]
            dd = cumdist(path); L = dd[-1]
            if L > best_len:
                best_len = L; best_path = path

    if not best_path or best_len <= 0:
        results[sid] = dict(flow_len_ft=None, elev_max_ft=None, elev_min_ft=None,
                            slope_lfp=None, slope_1085=None)
        continue

    dd = cumdist(best_path)
    rs, cs = best_path[0]; re_, ce = best_path[-1]
    emax, emin = DEM[rs][cs], DEM[re_][ce]
    L_ft = best_len * M_TO_FT
    slp  = abs(emax - emin) / best_len if best_len else 0.0
    s1085 = slope_1085(best_path, dd)
    results[sid] = dict(
        flow_len_ft=round(L_ft, 1),
        elev_max_ft=round(emax * M_TO_FT, 1),
        elev_min_ft=round(emin * M_TO_FT, 1),
        slope_lfp=round(slp, 5),
        slope_1085=round(s1085, 5))
    lines[sid] = [to_xy(r, c) for (r, c) in best_path]
    print("  id %s: L=%.0f ft  slope_lfp=%.2f%%  s1085=%.2f%%" %
          (sid, L_ft, 100 * slp, 100 * s1085))

# --- write the flow-path line layer ----------------------------------------
flds = QgsFields(); flds.append(QgsField("id", QVariant.Int))
flds.append(QgsField("flow_len_ft", QVariant.Double))
if os.path.exists(lfp_path):
    os.remove(lfp_path)
opts = QgsVectorFileWriter.SaveVectorOptions()
opts.driverName = "GPKG"; opts.layerName = LFP_LAYER
w = QgsVectorFileWriter.create(lfp_path, flds, QgsWkbTypes.LineString,
                               subws.crs(), QgsCoordinateTransformContext(), opts)
for sid, pts in lines.items():
    f = QgsFeature(flds)
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in pts]))
    f["id"] = int(sid); f["flow_len_ft"] = results[sid]["flow_len_ft"]
    w.addFeature(f)
del w

# --- append columns to params gpkg in place --------------------------------
pl = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, PARAMS_LAYER, "ogr")
pl.startEditing()
newcols = ["flow_len_ft", "elev_max_ft", "elev_min_ft", "slope_lfp", "slope_1085"]
have = [f.name() for f in pl.fields()]
for col in newcols:
    if col not in have:
        pl.dataProvider().addAttributes([QgsField(col, QVariant.Double)])
pl.updateFields()
idx = {col: pl.fields().indexFromName(col) for col in newcols}
for ft in pl.getFeatures():
    res = results.get(int(ft["id"]) if ft["id"] is not None else None)
    if not res:
        continue
    for col in newcols:
        pl.changeAttributeValue(ft.id(), idx[col], res[col])
pl.commitChanges()

print("\nWrote %s and updated %s." % (LFP_NAME, PARAMS_NAME))
if ADD_TO_PROJECT:
    for path, lyr, nm in [(lfp_path, LFP_LAYER, LFP_LAYER),
                          (params, PARAMS_LAYER, PARAMS_LAYER)]:
        v = QgsVectorLayer(path + "|layername=" + lyr, nm, "ogr")
        if v.isValid():
            QgsProject.instance().addMapLayer(v)
print("\nDone.")

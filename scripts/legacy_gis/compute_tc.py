# =============================================================================
# compute_tc.py   (QGIS Python Console)
#
# Step: compute time of concentration (Tc) for each subwatershed by three
# fully-DEM methods, and the timing parameters HEC-HMS needs, appending all to
# subwatershed_params.gpkg. Pure calculation -- reads the columns already in the
# file (area_km2, CN, slope_pct, flow_len_ft, slope_1085), no GIS step.
#
# Methods (US units; L in feet, S as ft/ft fraction, A in sq mi, CN dimensionless):
#   NRCS Lag   -> uses slope_pct (average basin slope); the method consistent
#                 with the CN / SCS unit-hydrograph framework. PRIMARY by default.
#   Kirpich    -> uses slope_1085 (10-85 flow-path slope); small-rural-channel
#                 empirical. Comparison/bracket (tends shortest).
#   Bransby-W. -> uses slope_1085; channel-dominated empirical (tends longest).
# Each was calibrated with the slope definition assigned to it.
#
# Minimum Tc floor: MIN_TC_MIN (default 6 min = 0.1 hr, common NRCS practice).
# Applied to every method; the RAW (unfloored) value is kept too, and a flag
# marks where the floor was hit, so nothing is hidden.
#
# HEC-HMS outputs (from the PRIMARY method's Tc):
#   tc_min    time of concentration (min)  -> Clark UH transform input
#   lag_min   lag time (min) = 0.6 * Tc    -> SCS UH transform input
# Both are written so either transform can be used.
#
# Columns appended (IN PLACE; CN/slope/flow-length preserved):
#   tc_nrcs_min, tc_kirpich_min, tc_bransby_min   (floored)
#   tc_nrcs_raw, tc_kirpich_raw, tc_bransby_raw    (unfloored, for the record)
#   tc_primary_min, tc_min, lag_min, tc_floored_flag, tc_method
#
# Run from: QGIS -> Plugins -> Python Console.
# =============================================================================
import os
from qgis.core import QgsProject, QgsVectorLayer, QgsField
from qgis.PyQt.QtCore import QVariant

# --- settings (set ROOT + SITE_DIR ONCE) -----------------------------------
try:
    ROOT
except NameError:
    ROOT = "C:/Users/smnfa/Dropbox/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

PARAMS_NAME  = "subwatershed_params.gpkg"
PARAMS_LAYER = "subwatershed_params"

PRIMARY_METHOD = "nrcs"        # "nrcs" | "kirpich" | "bransby" -> feeds lag/Tc
MIN_TC_MIN     = 6.0           # minimum Tc floor (minutes); 6 min = 0.1 hr
RELOAD_IN_PROJECT = True
# ---------------------------------------------------------------------------

KM2_TO_SQMI = 0.386102

def nrcs_lag_tc(L_ft, slope_frac, CN):
    """NRCS Lag -> Tc (min). slope = average basin slope (ft/ft). Tc = lag/0.6."""
    Y = slope_frac * 100.0                      # equation uses slope in PERCENT
    if Y <= 0 or CN <= 0 or L_ft <= 0:
        return None
    tlag_hr = (L_ft ** 0.8) * ((1000.0 / CN - 9) ** 0.7) / (1900.0 * (Y ** 0.5))
    return (tlag_hr / 0.6) * 60.0

def kirpich_tc(L_ft, slope_frac):
    """Kirpich Tc (min). slope = 10-85 flow-path slope (ft/ft). No surface factor."""
    if slope_frac <= 0 or L_ft <= 0:
        return None
    return 0.0078 * (L_ft ** 0.77) * (slope_frac ** -0.385)

def bransby_tc(L_ft, area_km2, slope_frac):
    """Bransby-Williams Tc (min). slope = 10-85 (ft/ft)."""
    if slope_frac <= 0 or area_km2 <= 0 or L_ft <= 0:
        return None
    L_km = L_ft * 0.0003048
    return 21.3 * L_km / ((area_km2 ** 0.1) * (slope_frac ** 0.2))

site = os.path.join(ROOT, SITE_DIR)
params = os.path.join(site, "outputs", PARAMS_NAME)
if not os.path.isfile(params):
    raise Exception("params not found: " + params)

lyr = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, PARAMS_LAYER, "ogr")
if not lyr.isValid():
    raise Exception("could not open params layer")

need = ["area_km2", "CN", "slope_pct", "flow_len_ft", "slope_1085"]
have = [f.name() for f in lyr.fields()]
missing = [c for c in need if c not in have]
if missing:
    raise Exception("params missing required columns: %s\n(run slope + flow-path scripts first)" % missing)

newcols = [
    ("tc_nrcs_min", QVariant.Double), ("tc_kirpich_min", QVariant.Double),
    ("tc_bransby_min", QVariant.Double), ("tc_nrcs_raw", QVariant.Double),
    ("tc_kirpich_raw", QVariant.Double), ("tc_bransby_raw", QVariant.Double),
    ("tc_primary_min", QVariant.Double), ("tc_min", QVariant.Double),
    ("lag_min", QVariant.Double), ("tc_floored", QVariant.Int),
    ("tc_method", QVariant.String),
]

lyr.startEditing()
for nm, ty in newcols:
    if nm not in have:
        lyr.dataProvider().addAttributes([QgsField(nm, ty)])
lyr.updateFields()
idx = {nm: lyr.fields().indexFromName(nm) for nm, _ in newcols}

print("Primary method:", PRIMARY_METHOD, " | floor:", MIN_TC_MIN, "min\n")
print(" id   NRCS  Kirpich Bransby   tc_min  lag_min  floored")

for ft in lyr.getFeatures():
    A   = ft["area_km2"]; CN = ft["CN"]
    Sp  = ft["slope_pct"]; L = ft["flow_len_ft"]; S85 = ft["slope_1085"]
    # slope_pct stored as PERCENT by extract_slope.py -> convert to fraction
    Sp_frac = (Sp / 100.0) if Sp is not None else None

    raw = {
        "nrcs":    nrcs_lag_tc(L, Sp_frac, CN) if None not in (L, Sp_frac, CN) else None,
        "kirpich": kirpich_tc(L, S85) if None not in (L, S85) else None,
        "bransby": bransby_tc(L, A, S85) if None not in (L, A, S85) else None,
    }
    def fl(v):
        return max(v, MIN_TC_MIN) if v is not None else None
    flo = {k: fl(v) for k, v in raw.items()}

    primary = flo.get(PRIMARY_METHOD)
    floored = 1 if (raw.get(PRIMARY_METHOD) is not None
                    and raw[PRIMARY_METHOD] < MIN_TC_MIN) else 0
    tc_min = primary
    lag_min = (0.6 * primary) if primary is not None else None

    def setv(col, v):
        lyr.changeAttributeValue(ft.id(), idx[col],
                                 round(v, 2) if isinstance(v, float) else v)
    setv("tc_nrcs_min", flo["nrcs"]); setv("tc_kirpich_min", flo["kirpich"]); setv("tc_bransby_min", flo["bransby"])
    setv("tc_nrcs_raw", raw["nrcs"]); setv("tc_kirpich_raw", raw["kirpich"]); setv("tc_bransby_raw", raw["bransby"])
    setv("tc_primary_min", primary); setv("tc_min", tc_min); setv("lag_min", lag_min)
    lyr.changeAttributeValue(ft.id(), idx["tc_floored"], floored)
    lyr.changeAttributeValue(ft.id(), idx["tc_method"], PRIMARY_METHOD)

    print(" %-3s %6s %7s %7s   %6s %7s    %s" % (
        ft["id"],
        "%.1f" % flo["nrcs"]    if flo["nrcs"]    else "-",
        "%.1f" % flo["kirpich"] if flo["kirpich"] else "-",
        "%.1f" % flo["bransby"] if flo["bransby"] else "-",
        "%.1f" % tc_min  if tc_min  else "-",
        "%.1f" % lag_min if lag_min else "-",
        "yes" if floored else ""))

lyr.commitChanges()
print("\nUpdated", PARAMS_NAME, "with Tc / lag columns.")

if RELOAD_IN_PROJECT:
    v = QgsVectorLayer(params + "|layername=" + PARAMS_LAYER, PARAMS_LAYER, "ogr")
    if v.isValid():
        QgsProject.instance().addMapLayer(v)
print("\nDone.")

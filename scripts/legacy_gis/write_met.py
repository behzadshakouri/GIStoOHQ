# =============================================================================
# write_met.py (QGIS Python Console)
#
# NHA WS3 -- generate HEC-HMS meteorologic model files from Atlas 14 data.
#
# ARCHITECTURE (one set of files per storm):
#   <model_name>.met   -- Meteorology header + subbasin assignments
#   <model_name>.gage  -- Gage definitions (one gage per storm)
#   HMS_NAME.dss       -- Hyetograph time series (via pydsstools)
#
# Two storms produced:
#   <HMS_NAME>_6hr_100yr   6-hour, 100-year
#   <HMS_NAME>_24hr_100yr  24-hour, 100-year
#
# READS
#   <SITE>/atlas14/atlas14_pf.csv   Atlas 14 frequency table
#   outputs/topology.gpkg           subbasin name list
#
# WRITES (all to outputs/)
#   <HMS_NAME>_6hr_100yr.met
#   <HMS_NAME>_6hr_100yr.gage
#   <HMS_NAME>_24hr_100yr.met
#   <HMS_NAME>_24hr_100yr.gage
#   <HMS_NAME>.dss   (contains time series for both storms)
#
# STORM TYPE OPTIONS:
#   "atlas14"   -- Atlas 14 incremental hyetograph, alternating-block, peak@40%
#   "scs_type2" -- SCS Type II dimensionless distribution
#
# REQUIRES: pydsstools  (pip install pydsstools --break-system-packages)
#
# Run from: QGIS -> Plugins -> Python Console
# =============================================================================

import os
import csv
import sys
from datetime import datetime
from qgis.core import QgsVectorLayer

# Ensure user site-packages is on path (needed when QGIS doesn't include it)
import site
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# ---------------------------------------------------------------------------
# --- settings --------------------------------------------------------------
# ---------------------------------------------------------------------------

try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"

try:
    STORM_TYPE
except NameError:
    STORM_TYPE = "atlas14"

RETURN_PERIOD = 100

# ---------------------------------------------------------------------------
# --- derived paths ---------------------------------------------------------
# ---------------------------------------------------------------------------

BASIN_NAME = os.path.basename(os.path.normpath(SITE_DIR))
HMS_NAME   = BASIN_NAME.replace("-", "_")
site_path  = os.path.join(ROOT, SITE_DIR)
OUT_DIR    = os.path.join(site_path, "outputs")   # for topology.gpkg and atlas14/
PF_CSV     = os.path.join(site_path, "atlas14", "atlas14_pf.csv")
TOPO_PATH  = os.path.join(OUT_DIR, "topology.gpkg")

# All HMS files written directly to the HMS project folder
try:
    HMS_DIR
except NameError:
    HMS_DIR = os.path.join(ROOT, "WS3_HMS")

HMS_PROJ_DIR = os.path.join(HMS_DIR, HMS_NAME)
os.makedirs(HMS_PROJ_DIR, exist_ok=True)

DSS_PATH = os.path.join(HMS_PROJ_DIR, HMS_NAME + ".dss")

CRLF = "\r\n"

# ---------------------------------------------------------------------------
# --- helpers ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def blk(lines):
    return CRLF.join(lines) + CRLF

now  = datetime.now()
DATE = now.strftime("%d %B %Y")
TIME = now.strftime("%H:%M:%S")

# Simulation start anchor (arbitrary for design storms)
SIM_START_STR = "01JAN2000:0006"   # HecTime format, end of first 6-min interval
SIM_START_HMS = "1 January 2000, 00:00"
SIM_START_DSS = "01Jan2000"        # DSS D-part -- must match pydsstools output exactly

# ---------------------------------------------------------------------------
# --- read Atlas 14 table ---------------------------------------------------
# ---------------------------------------------------------------------------

if not os.path.isfile(PF_CSV):
    raise Exception("atlas14_pf.csv not found:\n  %s" % PF_CSV)

pf = {}
with open(PF_CSV, newline="") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
        dur = row["duration"].strip()
        pf[dur] = {}
        for k, v in row.items():
            if k == "duration":
                continue
            try:
                pf[dur][k.strip()] = float(v)
            except (ValueError, TypeError):
                pass

rp_key = "%dyr" % RETURN_PERIOD

# Auto-detect the actual return period key format in the CSV.
# atlas14_pf.csv written by Atlas14Client uses "2yr","5yr",...,"100yr".
# Verify the key exists; if not, try without "yr" suffix.
sample_dur = list(pf.keys())[0] if pf else None
if sample_dur:
    available_rp_keys = list(pf[sample_dur].keys())
    print("  CSV return period keys: %s" % available_rp_keys)
    if rp_key not in available_rp_keys:
        # Try bare number
        alt_key = str(RETURN_PERIOD)
        if alt_key in available_rp_keys:
            rp_key = alt_key
            print("  Using return period key: '%s'" % rp_key)
        else:
            raise Exception(
                "Return period key '%s' not found in atlas14_pf.csv.\n"
                "Available keys: %s" % (rp_key, available_rp_keys))
    else:
        print("  Using return period key: '%s'" % rp_key)

def get_depth(dur, rp=rp_key):
    return pf.get(dur, {}).get(rp, None)

# ---------------------------------------------------------------------------
# --- read subbasin names ---------------------------------------------------
# ---------------------------------------------------------------------------

if not os.path.isfile(TOPO_PATH):
    raise Exception("topology.gpkg not found -- run build_topology.py first")

topo = QgsVectorLayer(TOPO_PATH + "|layername=topology", "topo", "ogr")
if not topo.isValid():
    raise Exception("Could not open topology.gpkg")

subbasin_names = sorted(
    f["name"] for f in topo.getFeatures()
    if f["name"] and str(f["name"]).startswith("Subbasin_")
)
print("Subbasins: %d" % len(subbasin_names))

# ---------------------------------------------------------------------------
# --- hyetograph builders ---------------------------------------------------
# ---------------------------------------------------------------------------

ATLAS14_DURS = [
    ("5min",  5), ("10min", 10), ("15min", 15), ("30min", 30),
    ("60min", 60), ("2hr", 120), ("3hr", 180), ("6hr", 360),
    ("12hr", 720), ("24hr", 1440),
]

SCS_TYPE2_CUMULATIVE = [
    (0,0.000),(30,0.011),(60,0.022),(90,0.035),(120,0.048),(150,0.063),
    (180,0.080),(210,0.098),(240,0.120),(270,0.147),(300,0.181),(330,0.235),
    (360,0.663),(390,0.772),(420,0.820),(450,0.854),(480,0.880),(510,0.898),
    (540,0.914),(570,0.926),(600,0.936),(630,0.946),(660,0.955),(690,0.962),
    (720,0.968),(750,0.973),(780,0.977),(810,0.981),(840,0.984),(870,0.986),
    (900,0.988),(930,0.990),(960,0.992),(990,0.993),(1020,0.994),(1050,0.995),
    (1080,0.996),(1110,0.997),(1140,0.998),(1170,0.999),(1200,0.999),
    (1230,1.000),(1260,1.000),(1290,1.000),(1320,1.000),(1350,1.000),
    (1380,1.000),(1410,1.000),(1440,1.000),
]

def build_atlas14_hyetograph(storm_dur_min, total_depth_in):
    breakpoints = [(0, 0.0)]
    for label, dur_min in ATLAS14_DURS:
        if dur_min > storm_dur_min:
            break
        d = get_depth(label)
        if d is not None:
            breakpoints.append((dur_min, d))

    # The last breakpoint should be the full-storm depth at storm_dur_min.
    # If the last Atlas 14 duration matches storm_dur_min exactly, use it directly.
    # Otherwise append the target depth as the endpoint.
    if breakpoints[-1][0] < storm_dur_min:
        breakpoints.append((storm_dur_min, total_depth_in))
    elif breakpoints[-1][1] != total_depth_in:
        # Last breakpoint is at storm_dur_min but value differs -- replace it
        breakpoints[-1] = (storm_dur_min, total_depth_in)
    # breakpoints now ends at (storm_dur_min, total_depth_in) -- no scaling needed

    # Verify monotonically increasing depths (required for valid hyetograph).
    for i in range(1, len(breakpoints)):
        if breakpoints[i][1] < breakpoints[i-1][1]:
            raise Exception(
                "Non-monotonic Atlas 14 breakpoints for %d-min storm:\n  %s\n"
                "Check atlas14_pf.csv return period key and duration order."
                % (storm_dur_min, breakpoints))

    dt = 6
    n_steps = storm_dur_min // dt
    cum = []
    for i in range(n_steps + 1):
        t = i * dt
        for j in range(len(breakpoints) - 1):
            t0, d0 = breakpoints[j]
            t1, d1 = breakpoints[j + 1]
            if t0 <= t <= t1:
                frac = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                cum.append(d0 + frac * (d1 - d0))
                break
        else:
            cum.append(breakpoints[-1][1])

    increments = [cum[i+1] - cum[i] for i in range(len(cum) - 1)]
    peak_idx = max(0, min(int(round(0.40 * n_steps)) - 1, n_steps - 1))
    sorted_inc = sorted(increments, reverse=True)
    arranged = [0.0] * n_steps

    # Place largest increment at peak_idx, then alternate left/right.
    # When one side is exhausted continue filling the other side only.
    positions = [peak_idx]
    lo, hi = peak_idx - 1, peak_idx + 1
    while len(positions) < n_steps:
        if hi < n_steps and lo >= 0:
            # Both sides available -- alternate: right on even count, left on odd
            if len(positions) % 2 == 1:
                positions.append(hi)
                hi += 1
            else:
                positions.append(lo)
                lo -= 1
        elif hi < n_steps:
            positions.append(hi)
            hi += 1
        else:
            positions.append(lo)
            lo -= 1

    for pos, val in zip(positions, sorted_inc):
        arranged[pos] = val
    return arranged  # list of incremental depths, length = n_steps

def build_scs_type2_hyetograph(storm_dur_min, total_depth_in):
    dt = 6
    n_steps = storm_dur_min // dt
    scale_time = storm_dur_min / 1440.0
    cum_frac = []
    for i in range(n_steps + 1):
        t_scaled = (i * dt) / scale_time
        for j in range(len(SCS_TYPE2_CUMULATIVE) - 1):
            t0, f0 = SCS_TYPE2_CUMULATIVE[j]
            t1, f1 = SCS_TYPE2_CUMULATIVE[j + 1]
            if t0 <= t_scaled <= t1:
                frac = (t_scaled - t0) / (t1 - t0) if t1 > t0 else 0.0
                cum_frac.append(f0 + frac * (f1 - f0))
                break
        else:
            cum_frac.append(1.0)
    return [(cum_frac[i+1] - cum_frac[i]) * total_depth_in
            for i in range(n_steps)]

def build_hyetograph(storm_dur_min, total_depth_in):
    if STORM_TYPE == "scs_type2":
        return build_scs_type2_hyetograph(storm_dur_min, total_depth_in)
    return build_atlas14_hyetograph(storm_dur_min, total_depth_in)

# ---------------------------------------------------------------------------
# --- DSS writer ------------------------------------------------------------
# ---------------------------------------------------------------------------

def write_dss(dss_path, pathname, increments):
    """Write incremental precipitation time series to DSS file.
    Deletes the DSS file on first call to avoid stale data from previous runs.
    """
    try:
        import pydsstools._lib.x64.core_heclib as cl
        from pydsstools.heclib.dss import HecDss
        from pydsstools.core import HecTime
        import numpy as np
    except ImportError:
        raise Exception(
            "pydsstools not found. Install with:\n"
            "  pip install pydsstools --break-system-packages")

    # Delete on first call (tracked by module-level flag) so stale pathnames
    # from previous runs don't persist. Both storms share one DSS file, so
    # only delete before the first storm is written.
    if not write_dss._deleted and os.path.isfile(dss_path):
        os.remove(dss_path)
        print("  Deleted stale DSS: %s" % os.path.basename(dss_path))
    write_dss._deleted = True

    vals = np.array(increments, dtype=np.float32)
    n = len(vals)
    tsc = cl.TimeSeriesContainer(
        pathname, n, 6,
        data_units="IN",
        data_type="PER-CUM",
        start_time=HecTime(SIM_START_STR, granularity=1),
        values=vals
    )
    with HecDss.Open(dss_path) as fid:
        fid.put_ts(tsc)

write_dss._deleted = False  # reset flag on script load

# ---------------------------------------------------------------------------
# --- .met file writer ------------------------------------------------------
# ---------------------------------------------------------------------------

def write_met_file(met_path, model_name, gage_name, subbasin_names):
    """
    Write one .met file for one storm model.
    Format matched exactly to HMS 4.13 output for Specified Hyetograph.
    Note: HMS writes 'Specified Average' in the file even when UI shows
    'Specified Hyetograph' -- these are the same method in the file format.
    """
    lines = [
        "Meteorology: " + model_name,
        "     Description: NOAA Atlas 14 Vol.1 Semiarid SW, %d-yr, type: %s"
            % (RETURN_PERIOD, STORM_TYPE),
        "     Last Modified Date: " + DATE,
        "     Last Modified Time: " + TIME,
        "     Version: 4.13",
        "     Unit System: English",
        "     Set Missing Data to Default: No",
        "     Precipitation Method: Specified Average",
        "     Air Temperature Method: None",
        "     Atmospheric Pressure Method: None",
        "     Dew Point Method: None",
        "     Wind Speed Method: None",
        "     Shortwave Radiation Method: None",
        "     Longwave Radiation Method: None",
        "     Snowmelt Method: None",
        "     Evapotranspiration Method: No Evapotranspiration",
        "     Use Basin Model: " + HMS_NAME,
        "End:",
        "",
        "Precip Method Parameters: Specified Average",
        "     Last Modified Date: " + DATE,
        "     Last Modified Time: " + TIME,
        "     Allow Depth Override: No",
        "End:",
        "",
    ]
    for sb in subbasin_names:
        lines += [
            "Subbasin: " + sb,
            "     Last Modified Date: " + DATE,
            "     Last Modified Time: " + TIME,
            "     Gage: " + gage_name,
            "End:",
            "",
        ]
    with open(met_path, "w", newline="") as fh:
        fh.write(CRLF.join(lines))

# ---------------------------------------------------------------------------
# --- .gage file writer -----------------------------------------------------
# ---------------------------------------------------------------------------

def write_gage_file(gage_path, gage_name, dss_filename, dss_pathname,
                    storm_dur_min):
    """
    Write one .gage file for one storm's precipitation gage.
    Structure matched to HMS 4.13 AZ12_100.gage output.
    """
    end_min = storm_dur_min
    # End time: start + storm duration
    # Start: 01 January 2000, 00:00  End: depends on duration
    from datetime import timedelta
    from datetime import datetime as dt
    start = dt(2000, 1, 1, 0, 0)
    end   = start + timedelta(minutes=end_min)
    end_str = end.strftime("%-d %B %Y, %H:%M")

    lines = [
        "Gage Manager: ",
        "     Gage Manager: ",
        "     Version: 4.13",
        "     Filepath Separator: /",
        "End: ",
        "",
        "Gage: " + gage_name,
        "     Gage: " + gage_name,
        "     Gage Type: Precipitation",
        "     Description: NOAA Atlas 14, %d-yr, type: %s"
            % (RETURN_PERIOD, STORM_TYPE),
        "     Last Modified Date: " + DATE,
        "     Last Modified Time: " + TIME,
        "     Latitude Degrees: 0.0",
        "     Longitude Degrees: 0.0",
        "     Reference Height Units: Feet",
        "     Reference Height: 0.0",
        "     Data Source Type: Manual Entry",
        "     Filename: " + dss_filename,
        "     Pathname: " + dss_pathname,
        "     Variant: Variant-1",
        "       Start Time: " + SIM_START_HMS,
        "       End Time: " + end_str,
        "     End Variant: Variant-1",
        "End: ",
    ]
    with open(gage_path, "w", newline="") as fh:
        fh.write(CRLF.join(lines))

# ---------------------------------------------------------------------------
# --- storm loop ------------------------------------------------------------
# ---------------------------------------------------------------------------

STORMS = [
    ("6hr_100yr",  360,  "6hr"),
    ("24hr_100yr", 1440, "24hr"),
]

written = []

for suffix, dur_min, atlas_key in STORMS:
    total_depth = get_depth(atlas_key, rp_key)
    if total_depth is None:
        print("WARNING: depth not found for %s / %s -- skipping" % (atlas_key, rp_key))
        continue

    model_name = "%s_%s" % (HMS_NAME, suffix)
    gage_name  = model_name + "_Gage"
    met_path   = os.path.join(HMS_PROJ_DIR, model_name + ".met")
    gage_path  = os.path.join(HMS_PROJ_DIR, model_name + ".gage")

    # DSS pathname: /A/B/C/D/E/F/
    # A=project, B=gage_name, C=PRECIP-INC, D=date_block, E=interval, F=source
    # D-part is left BLANK so pydsstools auto-splits the series across day
    # blocks (01Jan2000, 02Jan2000, ...) when it exceeds 240 intervals.
    # This is required for the 24-hr storm + recession tail which exceeds
    # one day block. HMS reads the multi-block series seamlessly.
    dss_pathname = "/%s/%s/PRECIP-INC//6Minute/ATLAS14/" % (
        HMS_NAME, gage_name)
    dss_filename = HMS_NAME + ".dss"

    print("Storm: %-30s  depth=%.2f in  dur=%d min" % (model_name, total_depth, dur_min))

    # Build hyetograph
    increments = build_hyetograph(dur_min, total_depth)
    real_total = sum(increments)
    real_peak  = max(increments)

    # Pad with explicit zeros to cover the full control-spec window.
    # The control spec runs for storm_dur + 50% tail; DSS cells beyond the
    # storm must be explicit zeros or HMS reads them as "missing" and aborts.
    # Pad generously to 2x the storm duration (well past any control window).
    tail_intervals = int((dur_min * 1.5) // 6) + 10   # storm + 50% tail + margin
    block_intervals = (1440 // 6)                      # one full day block = 240
    target_intervals = max(tail_intervals, block_intervals)
    if len(increments) < target_intervals:
        increments = increments + [0.0] * (target_intervals - len(increments))

    print("  Hyetograph: %d real intervals (padded to %d), total=%.3f in, peak=%.3f in"
          % (dur_min // 6, len(increments), real_total, real_peak))

    # Write DSS
    write_dss(DSS_PATH, dss_pathname, increments)
    print("  DSS written: %s  path=%s" % (dss_filename, dss_pathname))

    # Write .met
    write_met_file(met_path, model_name, gage_name, subbasin_names)
    print("  .met written: %s" % os.path.basename(met_path))

    # Write .gage
    write_gage_file(gage_path, gage_name, dss_filename, dss_pathname, dur_min)
    print("  .gage written: %s" % os.path.basename(gage_path))

    written.append((model_name, met_path, gage_path))

if not written:
    raise Exception("No storms written. Check atlas14_pf.csv at:\n  %s" % PF_CSV)

print("\n--- Summary ---")
print("Storms written: %d" % len(written))
print("DSS file: %s" % DSS_PATH)
print("Storm type: %s | Return period: %d-yr" % (STORM_TYPE, RETURN_PERIOD))
print("Subbasins assigned: %d" % len(subbasin_names))
print("\nPROVENANCE: depths from NOAA Atlas 14 Vol.1 Semiarid SW, partial-duration series")
print("PRE-SEAL: confirm storm type against RFP #660.")

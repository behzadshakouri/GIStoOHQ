# =============================================================================
# hms_dss_to_gnuplot.py   (QGIS Python Console)
#
# Read FLOW records from the HMS output DSS file(s) and write them in a form
# plottable with gnuplot: one block-format .dat per GROUP (one curve per record,
# blocks separated by two blank lines) plus a long CSV for analysis. Time axis
# is HOURS FROM START. Grouping is controlled by a config.json.
#
# INPUT
#   <config.json>                     selection + grouping (see hms_dss_config.json)
#   WS3_HMS/<HMS_NAME>/<name>.dss     HMS run output DSS (FLOW time series)
#
# OUTPUT (in WS3_HMS/<HMS_NAME>/dss_plots/)
#   <group>.gp.dat   gnuplot blocks: one index per record, "hours  flow_cfs"
#   <group>.csv      long: group, location, pathname, hours, flow_cfs
#   flows_all.csv    every extracted record (one long table)
#   plot_hydrographs.gp is the companion gnuplot script (separate file)
#
# REQUIRES: pydsstools (already in QGIS Python from the HMS pipeline).
#
# Run from: QGIS -> Plugins -> Python Console:
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   CONFIG = "/home/arash/Dropbox/Chloeta/NHA/PythonScripts/hms_dss_config.json"
#   exec(open(SCRIPT_DIR + "/hms_dss_to_gnuplot.py").read())
# =============================================================================
import os
import sys
import csv
import json
import fnmatch

# pydsstools lives in the user site-packages (same as the HMS scripts)
import site
_us = site.getusersitepackages()
if _us not in sys.path:
    sys.path.insert(0, _us)

# --- root resolution -------------------------------------------------------
try:
    ROOT
except NameError:
    ROOT = "/home/arash/Dropbox/Chloeta/NHA/"
try:
    SITE_DIR
except NameError:
    SITE_DIR = "WS3_GIS/AZ12-100"
try:
    HMS_DIR
except NameError:
    HMS_DIR = os.path.join(ROOT, "WS3_HMS")
try:
    CONFIG
except NameError:
    CONFIG = os.path.join(ROOT, "PythonScripts", "hms_dss_config.json")

# --- derived ---------------------------------------------------------------
BASIN_NAME = os.path.basename(os.path.normpath(SITE_DIR))
HMS_NAME   = BASIN_NAME.replace("-", "_")
HMS_PROJ   = os.path.join(HMS_DIR, HMS_NAME)
OUT_DIR    = os.path.join(HMS_PROJ, "dss_plots")
os.makedirs(OUT_DIR, exist_ok=True)

NODATA_F32 = -3.4028235e+38     # pydsstools float32 sentinel in .values padding

print("=" * 70)
print("HMS DSS -> gnuplot   (FLOW records, hours from start)")
print("  HMS project :", HMS_PROJ)
print("  config      :", CONFIG)
print("  out dir     :", OUT_DIR)
print("=" * 70)

# --- load config -----------------------------------------------------------
if not os.path.isfile(CONFIG):
    raise Exception("config not found: " + CONFIG)
with open(CONFIG) as fh:
    cfg = json.load(fh)

rec_type = cfg.get("record_type", "FLOW").upper()
groups   = cfg.get("groups", [{"name": "all", "title": "All %s" % rec_type,
                               "match": ["*"]}])
dss_list = cfg.get("dss_files") or [HMS_NAME + ".dss"]
dss_paths = [os.path.join(HMS_PROJ, d) for d in dss_list]
for d in dss_paths:
    if not os.path.isfile(d):
        raise Exception("DSS not found: " + d)

# --- pydsstools ------------------------------------------------------------
try:
    from pydsstools.heclib.dss import HecDss
except ImportError:
    raise Exception("pydsstools not found. Install with:\n"
                    "  pip install pydsstools --break-system-packages")

def bpart(pathname):
    """B-part (location) of an /A/B/C/D/E/F/ DSS pathname."""
    parts = pathname.split("/")
    return parts[2] if len(parts) >= 7 else pathname

def cpart(pathname):
    """C-part (parameter) of an /A/B/C/D/E/F/ DSS pathname."""
    parts = pathname.split("/")
    return parts[3] if len(parts) >= 7 else ""

def path_no_dpart(pathname):
    """Same pathname with a BLANK D-part (date). pydsstools reads the whole
       series across day-blocks when the D-part is empty, so multi-day storms
       (24-hr spanning two dates) come back as one continuous hydrograph."""
    parts = pathname.split("/")
    if len(parts) >= 7:
        parts[4] = ""      # D-part
    return "/".join(parts)

def is_nodata(v):
    return (v is None) or (v <= NODATA_F32 * 0.999) or (v != v)  # sentinel or NaN

def clean_pairs(ts):
    """Return [(hour_index_ok value)] -> list of (i, value) for VALID samples,
       skipping nodata anywhere (not breaking at the first one)."""
    vals = list(ts.values)
    return [(i, float(v)) for i, v in enumerate(vals) if not is_nodata(v)]

def valid_dt_value(ts):
    """List of (datetime, value) for valid (non-nodata) samples of a record.
       Uses real DSS times (HecTime.datetime()); falls back to interval-seconds
       from an assumed start if times are unavailable."""
    pairs = clean_pairs(ts)            # [(i, value), ...]
    if not pairs:
        return []
    # times
    try:
        tlist = list(ts.times)
        dts = [tlist[i].datetime() for (i, _v) in pairs]
        return list(zip(dts, [v for (_i, v) in pairs]))
    except Exception:
        pass
    # fallback: synthesize from interval (seconds) -- absolute time unknown, use
    # a fixed epoch so multi-block concatenation still orders correctly.
    from datetime import datetime, timedelta
    try:
        step_s = float(ts.interval)
    except Exception:
        step_s = 360.0
    if step_s <= 0:
        step_s = 360.0
    epoch = datetime(2000, 1, 1)
    return [(epoch + timedelta(seconds=step_s * i), v) for (i, v) in pairs]

def storm_tag(dss_filename):
    """Short storm label from the run DSS filename, e.g.
       'Run_6hr_100yr.dss' -> '6hr_100yr'. Used to distinguish the same
       location across storms in labels."""
    base = os.path.splitext(dss_filename)[0]
    for pre in ("Run_", "run_"):
        if base.startswith(pre):
            base = base[len(pre):]
    return base

# --- read every exact-FLOW record, stitched across date blocks -------------
records = []   # dict: dss, storm, location, label, hours[], flow[]
for dss in dss_paths:
    storm = storm_tag(os.path.basename(dss))
    with HecDss.Open(dss) as fid:
        try:
            allpaths = fid.search_path("")
        except Exception:
            allpaths = fid.getPathnameList("", sort=1)
        # exact C-part == rec_type ("FLOW")
        flow_paths = [p for p in allpaths if cpart(p) == rec_type]
        # group ALL dated paths by location (a location may have several date
        # blocks: 24-hr storm spans two days)
        by_loc = {}
        for p in flow_paths:
            by_loc.setdefault(bpart(p), []).append(p)
        print("  %s: %d exact-%s record(s) -> %d location(s)"
              % (os.path.basename(dss), len(flow_paths), rec_type, len(by_loc)))
        for loc, plist in by_loc.items():
            # read every date block for this location, collect valid (dt, value)
            merged = []
            for p in plist:
                try:
                    ts = fid.read_ts(p)          # dated path works reliably
                except Exception:
                    continue
                merged.extend(valid_dt_value(ts))
            if not merged:
                continue
            # sort by time, drop duplicate timestamps (block overlaps)
            merged.sort(key=lambda t: t[0])
            dedup = []
            seen_t = set()
            for (dt, v) in merged:
                if dt in seen_t:
                    continue
                seen_t.add(dt)
                dedup.append((dt, v))
            t0 = dedup[0][0]
            hrs  = [(dt - t0).total_seconds() / 3600.0 for (dt, _v) in dedup]
            flow = [v for (_dt, v) in dedup]
            records.append({
                "dss": os.path.basename(dss),
                "storm": storm,
                "location": loc,
                "label": "%s (%s)" % (loc, storm),
                "pathname": plist[0],
                "hours": hrs,
                "flow": flow,
                "units": "CFS",
            })

if not records:
    raise Exception("No %s records found in the DSS file(s)." % rec_type)
print("total %s records read: %d" % (rec_type, len(records)))

# --- write the master long CSV ---------------------------------------------
ALL_CSV = os.path.join(OUT_DIR, "flows_all.csv")
with open(ALL_CSV, "w", newline="") as fh:
    wr = csv.writer(fh)
    wr.writerow(["location", "storm", "pathname", "dss", "hours", "flow_cfs"])
    for r in records:
        for h, q in zip(r["hours"], r["flow"]):
            wr.writerow([r["location"], r["storm"], r["pathname"], r["dss"],
                         "%.4f" % h, "%.4f" % q])
print("wrote", ALL_CSV)

# --- per-group .dat + .csv -------------------------------------------------
def matches(loc, patterns):
    for pat in patterns:
        if fnmatch.fnmatch(loc, pat):
            return True
    return False

group_index = []    # (group_name, title, dat_path, [locations])
for grp in groups:
    gname  = grp["name"]
    gtitle = grp.get("title", gname)
    pats   = grp.get("match", ["*"])
    sel = [r for r in records if matches(r["location"], pats)]
    if not sel:
        print("  group '%s': no matching records (patterns %s)" % (gname, pats))
        continue
    # stable order: by location name
    sel.sort(key=lambda r: r["location"])

    dat = os.path.join(OUT_DIR, gname + ".gp.dat")
    with open(dat, "w", newline="") as fh:
        fh.write("# HMS %s hydrographs -- group '%s'\n" % (rec_type, gname))
        fh.write("# columns: hours  flow_cfs   (one index per record)\n")
        first = True
        for r in sel:
            if not first:
                fh.write("\n\n")
            first = False
            fh.write("# label=%s  location=%s  storm=%s  pathname=%s\n"
                     % (r["label"], r["location"], r["storm"], r["pathname"]))
            for h, q in zip(r["hours"], r["flow"]):
                fh.write("%.4f %.4f\n" % (h, q))

    gcsv = os.path.join(OUT_DIR, gname + ".csv")
    with open(gcsv, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["group", "location", "storm", "pathname", "hours", "flow_cfs"])
        for r in sel:
            for h, q in zip(r["hours"], r["flow"]):
                wr.writerow([gname, r["location"], r["storm"], r["pathname"],
                             "%.4f" % h, "%.4f" % q])

    locs = [r["location"] for r in sel]
    group_index.append((gname, gtitle, dat, locs))
    print("  group '%s': %d record(s) -> %s" % (gname, len(sel), os.path.basename(dat)))

# --- write a small manifest the gnuplot script can read --------------------
MANIFEST = os.path.join(OUT_DIR, "groups_manifest.txt")
with open(MANIFEST, "w") as fh:
    fh.write("# group_name | title | dat_file | n_curves\n")
    for (gname, gtitle, dat, locs) in group_index:
        fh.write("%s|%s|%s|%d\n" % (gname, gtitle, os.path.basename(dat), len(locs)))
print("wrote", MANIFEST)

print("\n" + "=" * 70)
print("DONE.")
print("=" * 70)
for (gname, gtitle, dat, locs) in group_index:
    print("  %-22s %2d curve(s)  ->  %s" % (gname, len(locs), os.path.basename(dat)))
print("\nPlot a group, e.g.:")
if group_index:
    g0 = group_index[0]
    print("  gnuplot -e \"dat='%s'; out='%s'\" plot_hydrographs.gp"
          % (g0[2], os.path.join(OUT_DIR, g0[0] + ".png")))
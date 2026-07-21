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
# --- obtain/read Atlas 14 table --------------------------------------------
# ---------------------------------------------------------------------------

def _site_coordinate():
    """
    Resolve the point used for the NOAA Atlas 14 query.

    Priority:
      1. Explicit QGIS-runner globals:
         ATLAS14_LAT/ATLAS14_LON, WATERSHED_LAT/WATERSHED_LON,
         SITE_LAT/SITE_LON, LAT/LON
      2. Environment variables ATLAS14_LAT and ATLAS14_LON
      3. Known Sligo Creek mouth coordinate
      4. Centroid of watershed_boundary.gpkg
      5. outputs/outlet.shp or outputs/outlet.gpkg
    """
    candidate_global_names = [
        ("ATLAS14_LAT", "ATLAS14_LON"),
        ("WATERSHED_LAT", "WATERSHED_LON"),
        ("SITE_LAT", "SITE_LON"),
        ("LAT", "LON"),
    ]

    namespace = globals()
    for lat_name, lon_name in candidate_global_names:
        if lat_name in namespace and lon_name in namespace:
            try:
                return (
                    float(namespace[lat_name]),
                    float(namespace[lon_name]),
                    "%s/%s" % (lat_name, lon_name),
                )
            except (TypeError, ValueError):
                pass

    try:
        env_lat = os.environ.get("ATLAS14_LAT")
        env_lon = os.environ.get("ATLAS14_LON")
        if env_lat not in (None, "") and env_lon not in (None, ""):
            return float(env_lat), float(env_lon), "environment"
    except (TypeError, ValueError):
        pass

    # The project runner resolves this same coordinate for Sligo Creek.
    if BASIN_NAME.lower().replace("_", "").replace("-", "") == "sligocreek":
        return 39.000215, -77.010810, "Sligo Creek watershed coordinate"

    try:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
            QgsVectorLayer,
        )

        vector_candidates = [
            (
                os.path.join(OUT_DIR, "watershed_boundary.gpkg"),
                "watershed_boundary",
            ),
            (os.path.join(OUT_DIR, "outlet.shp"), None),
            (os.path.join(OUT_DIR, "outlet.gpkg"), None),
        ]

        for vector_path, layer_name in vector_candidates:
            if not os.path.isfile(vector_path):
                continue

            uri = vector_path
            if layer_name:
                uri += "|layername=" + layer_name

            layer = QgsVectorLayer(uri, "atlas14_location", "ogr")
            if not layer.isValid():
                continue

            feature = next(layer.getFeatures(), None)
            if feature is None or feature.geometry().isEmpty():
                continue

            point = feature.geometry().centroid().asPoint()
            source_crs = layer.crs()
            target_crs = QgsCoordinateReferenceSystem("EPSG:4326")

            if source_crs.isValid() and source_crs != target_crs:
                transform = QgsCoordinateTransform(
                    source_crs,
                    target_crs,
                    QgsProject.instance(),
                )
                point = transform.transform(point)

            latitude = float(point.y())
            longitude = float(point.x())

            if -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0:
                return latitude, longitude, vector_path
    except Exception as exc:
        print("WARNING: could not derive Atlas 14 coordinate from GIS: %s" % exc)

    raise Exception(
        "Could not determine the Atlas 14 query coordinate. Set "
        "ATLAS14_LAT and ATLAS14_LON in the runner."
    )


def _normalize_noaa_duration(value):
    text = str(value).strip().lower()
    text = text.rstrip(":").replace(" ", "")
    text = text.replace("-", "")

    aliases = {
        "5min": "5min",
        "10min": "10min",
        "15min": "15min",
        "30min": "30min",
        "60min": "60min",
        "2hr": "2hr",
        "3hr": "3hr",
        "6hr": "6hr",
        "12hr": "12hr",
        "24hr": "24hr",
        "2day": "2day",
        "3day": "3day",
        "4day": "4day",
        "7day": "7day",
        "10day": "10day",
        "20day": "20day",
        "30day": "30day",
        "45day": "45day",
        "60day": "60day",
    }
    return aliases.get(text)


def _download_atlas14_pf_csv(csv_path):
    """
    Query NOAA's machine-readable PFDS point endpoint.

    This endpoint returns the point estimates, upper confidence bounds, and
    lower confidence bounds as CSV-like text. Only the point-estimate section
    is written to atlas14_pf.csv.
    """
    import json
    import time
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    latitude, longitude, coordinate_source = _site_coordinate()

    query = urlencode(
        {
            "lat": "%.6f" % latitude,
            "lon": "%.6f" % longitude,
            "type": "pf",
            "data": "depth",
            "units": "english",
            "series": "pds",
        }
    )

    # Current official endpoint. The older /cgi-bin/hdsc/new path is retained
    # as a fallback because NOAA has used both paths.
    endpoint_urls = [
        "https://hdsc.nws.noaa.gov/cgi-bin/new/fe_text.csv?" + query,
        "https://hdsc.nws.noaa.gov/cgi-bin/hdsc/new/fe_text.csv?" + query,
    ]

    print("atlas14_pf.csv is missing; downloading NOAA Atlas 14 data.")
    print(
        "  Coordinate: %.6f, %.6f (%s)"
        % (latitude, longitude, coordinate_source)
    )

    response_text = None
    source_url = None
    errors = []

    for endpoint_url in endpoint_urls:
        print("  PFDS endpoint:", endpoint_url)

        for attempt in range(1, 4):
            try:
                request = Request(
                    endpoint_url,
                    headers={
                        "User-Agent": (
                            "GIStoOHQ/0.1 "
                            "(NOAA Atlas 14 design-storm preparation)"
                        ),
                        "Accept": "text/csv,text/plain,*/*",
                    },
                )

                with urlopen(request, timeout=90) as response:
                    raw = response.read()

                # NOAA normally returns UTF-8/ASCII, but latin-1 keeps parsing
                # deterministic if a metadata character is present.
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")

                if "PRECIPITATION FREQUENCY ESTIMATES" not in text.upper():
                    raise Exception(
                        "response does not contain a precipitation-frequency "
                        "estimate section"
                    )

                response_text = text
                source_url = endpoint_url
                break

            except Exception as exc:
                errors.append("%s attempt %d: %s" %
                              (endpoint_url, attempt, exc))
                print("    attempt %d/3 failed: %s" % (attempt, exc))
                if attempt < 3:
                    time.sleep(2 * attempt)

        if response_text is not None:
            break

    if response_text is None:
        raise Exception(
            "Could not download NOAA Atlas 14 point estimates.\n"
            + "\n".join(errors)
        )

    debug_dir = os.path.dirname(csv_path)
    os.makedirs(debug_dir, exist_ok=True)

    raw_path = os.path.join(debug_dir, "atlas14_pfds_response.csv")
    with open(raw_path, "w", encoding="utf-8", newline="") as raw_file:
        raw_file.write(response_text)

    return_periods = [
        "1", "2", "5", "10", "25",
        "50", "100", "200", "500", "1000",
    ]

    required_durations = [
        "5min", "10min", "15min", "30min", "60min",
        "2hr", "3hr", "6hr", "12hr", "24hr",
    ]

    estimates = {}
    in_mean_section = False

    for raw_line in response_text.splitlines():
        line = raw_line.strip()
        upper = line.upper()

        # NOAA writes the point-estimate heading and the ARI header on
        # separate lines. Start at the first plain estimate heading, but do
        # not enter the upper/lower confidence-bound sections.
        if (
            upper.startswith("PRECIPITATION FREQUENCY ESTIMATES")
            and "UPPER BOUND" not in upper
            and "LOWER BOUND" not in upper
        ):
            in_mean_section = True
            continue

        if in_mean_section and (
            "PRECIPITATION FREQUENCY ESTIMATES AT UPPER BOUND" in upper
            or "PRECIPITATION FREQUENCY ESTIMATES AT LOWER BOUND" in upper
        ):
            break

        if not in_mean_section or "," not in line:
            continue

        fields = next(csv.reader([line]))
        if not fields:
            continue

        duration = _normalize_noaa_duration(fields[0])
        if duration is None:
            continue

        numeric_values = []
        for value in fields[1:]:
            value = str(value).strip()
            if not value:
                continue
            try:
                numeric_values.append(float(value))
            except ValueError:
                continue

        if len(numeric_values) >= len(return_periods):
            estimates[duration] = dict(
                zip(return_periods, numeric_values[:len(return_periods)])
            )

    missing = [
        duration
        for duration in required_durations
        if duration not in estimates or "100" not in estimates[duration]
    ]

    if missing:
        raise Exception(
            "NOAA returned data, but these required durations were not "
            "parsed: %s\nRaw response saved at:\n  %s"
            % (", ".join(missing), raw_path)
        )

    fieldnames = ["duration"] + [
        return_period + "yr" for return_period in return_periods
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()

        for duration in required_durations:
            output_row = {"duration": duration}
            for return_period in return_periods:
                value = estimates[duration].get(return_period)
                if value is not None:
                    output_row[return_period + "yr"] = value
            writer.writerow(output_row)

    metadata = {
        "source": "NOAA Atlas 14 Precipitation Frequency Data Server",
        "endpoint": source_url,
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_source": coordinate_source,
        "data": "precipitation depth",
        "series": "partial duration series",
        "units": "inches",
        "downloaded": datetime.now().isoformat(timespec="seconds"),
        "raw_response": raw_path,
    }

    metadata_path = os.path.join(
        os.path.dirname(csv_path),
        "atlas14_metadata.json",
    )
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2)

    print("  Wrote Atlas 14 table:", csv_path)
    print("  Wrote raw response  :", raw_path)
    print("  Wrote metadata      :", metadata_path)


if not os.path.isfile(PF_CSV) or os.path.getsize(PF_CSV) == 0:
    _download_atlas14_pf_csv(PF_CSV)
else:
    print("Reusing Atlas 14 table:", PF_CSV)

pf = {}
with open(PF_CSV, newline="", encoding="utf-8-sig") as fh:
    reader = csv.DictReader(fh)

    if not reader.fieldnames or "duration" not in reader.fieldnames:
        raise Exception(
            "Invalid atlas14_pf.csv; expected a 'duration' column:\n  %s"
            % PF_CSV
        )

    for row in reader:
        dur = str(row.get("duration", "")).strip()
        if not dur:
            continue

        pf[dur] = {}
        for key, value in row.items():
            if key is None or key == "duration":
                continue

            try:
                pf[dur][str(key).strip()] = float(value)
            except (ValueError, TypeError):
                pass

if not pf:
    raise Exception("No precipitation-frequency rows found in:\n  %s" % PF_CSV)

rp_key = "%dyr" % RETURN_PERIOD

sample_dur = next(iter(pf))
available_rp_keys = list(pf[sample_dur].keys())
print("  CSV return period keys: %s" % available_rp_keys)

if rp_key not in available_rp_keys:
    alternate_key = str(RETURN_PERIOD)
    if alternate_key in available_rp_keys:
        rp_key = alternate_key
    else:
        raise Exception(
            "Return period key '%s' not found in atlas14_pf.csv.\n"
            "Available keys: %s"
            % (rp_key, available_rp_keys)
        )

print("  Using return period key: '%s'" % rp_key)


def get_depth(dur, rp=rp_key):
    return pf.get(dur, {}).get(rp)

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

def _safe_series_name(pathname):
    parts = [part for part in pathname.split("/") if part]
    name = parts[1] if len(parts) >= 2 else "precipitation"
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


def _write_dss_fallback(dss_path, pathname, increments):
    """Write portable CSV data and a HEC-DSSVue Jython import script."""
    from datetime import datetime as _dt, timedelta as _td

    project_dir = os.path.dirname(dss_path)
    fallback_dir = os.path.join(project_dir, "dss_import")
    os.makedirs(fallback_dir, exist_ok=True)

    series_name = _safe_series_name(pathname)
    csv_path = os.path.join(fallback_dir, series_name + ".csv")
    script_path = os.path.join(project_dir, "import_hyetographs_dssvue.py")
    readme_path = os.path.join(project_dir, "DSS_IMPORT_REQUIRED.txt")

    start_time = _dt(2000, 1, 1, 0, 6)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["datetime", "incremental_precipitation_in"])
        for index, value in enumerate(increments):
            timestamp = start_time + _td(minutes=6 * index)
            writer.writerow([timestamp.strftime("%Y-%m-%d %H:%M"), "%.8f" % float(value)])

    if not write_dss._fallback_initialized:
        for stale_path in (script_path, readme_path, dss_path):
            if os.path.isfile(stale_path):
                os.remove(stale_path)
        write_dss._fallback_initialized = True

        header = (
            "# HEC-DSSVue Jython script generated by GIStoOHQ\n"
            "# Run in HEC-DSSVue: Tools > Script Editor > Open > Run.\n\n"
            "from hec.heclib.dss import HecDss\n"
            "from hec.io import TimeSeriesContainer\n"
            "from hec.heclib.util import HecTime\n"
            "import os\n\n"
            "DSS_FILE = %r\n" % dss_path
        )
        header += (
            "if os.path.exists(DSS_FILE):\n"
            "    os.remove(DSS_FILE)\n"
            "dss = HecDss.open(DSS_FILE)\n\n"
        )
        with open(script_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(header)

    values_repr = repr([round(float(value), 8) for value in increments])
    block = "\n# %s\n" % pathname
    block += "tsc = TimeSeriesContainer()\n"
    block += "tsc.fullName = %r\n" % pathname
    block += "tsc.interval = 6\n"
    block += "tsc.numberValues = %d\n" % len(increments)
    block += "tsc.units = 'IN'\n"
    block += "tsc.type = 'PER-CUM'\n"
    block += "start = HecTime('01JAN2000', '0006')\n"
    block += "times = []\n"
    block += "for i in range(tsc.numberValues):\n"
    block += "    times.append(start.value())\n"
    block += "    start.add(6)\n"
    block += "tsc.times = times\n"
    block += "tsc.values = %s\n" % values_repr
    block += "dss.put(tsc)\n"

    with open(script_path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(block)

    with open(readme_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "pydsstools was unavailable, so GIStoOHQ created portable storm CSV files.\n\n"
            "The .met and .gage files are complete, but HEC-HMS requires:\n  %s\n\n"
            "Create it by running this script in HEC-DSSVue:\n  %s\n\n"
            "Source CSV folder:\n  %s\n" % (dss_path, script_path, fallback_dir)
        )

    return {"mode": "fallback", "csv": csv_path, "script": script_path, "readme": readme_path}


def write_dss(dss_path, pathname, increments):
    """Write incremental precipitation to DSS or create portable fallback files."""
    try:
        import pydsstools._lib.x64.core_heclib as cl
        from pydsstools.heclib.dss import HecDss
        from pydsstools.core import HecTime
        import numpy as np
    except (ImportError, ModuleNotFoundError) as exc:
        if not write_dss._warned_fallback:
            print("  WARNING: pydsstools is unavailable: %s" % exc)
            print("  Writing CSV files and a HEC-DSSVue import script instead.")
            write_dss._warned_fallback = True
        return _write_dss_fallback(dss_path, pathname, increments)

    if not write_dss._deleted and os.path.isfile(dss_path):
        os.remove(dss_path)
        print("  Deleted stale DSS: %s" % os.path.basename(dss_path))
    write_dss._deleted = True

    vals = np.array(increments, dtype=np.float32)
    tsc = cl.TimeSeriesContainer(
        pathname, len(vals), 6,
        data_units="IN",
        data_type="PER-CUM",
        start_time=HecTime(SIM_START_STR, granularity=1),
        values=vals
    )
    with HecDss.Open(dss_path) as fid:
        fid.put_ts(tsc)
    return {"mode": "dss", "dss": dss_path}


write_dss._deleted = False
write_dss._warned_fallback = False
write_dss._fallback_initialized = False

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

    # Write DSS, or portable fallback products when pydsstools is absent.
    dss_result = write_dss(DSS_PATH, dss_pathname, increments)
    if dss_result["mode"] == "dss":
        print("  DSS written: %s  path=%s" % (dss_filename, dss_pathname))
    else:
        print("  DSS pending: run %s in HEC-DSSVue"
              % os.path.basename(dss_result["script"]))
        print("  Portable CSV: %s" % dss_result["csv"])

    # Write .met
    write_met_file(met_path, model_name, gage_name, subbasin_names)
    print("  .met written: %s" % os.path.basename(met_path))

    # Write .gage
    write_gage_file(gage_path, gage_name, dss_filename, dss_pathname, dur_min)
    print("  .gage written: %s" % os.path.basename(gage_path))

    written.append((model_name, met_path, gage_path))

if not written:
    raise Exception("No storms written. Check atlas14_pf.csv at:\n  %s" % PF_CSV)

if write_dss._fallback_initialized:
    fallback_script = os.path.join(HMS_PROJ_DIR, "import_hyetographs_dssvue.py")
    with open(fallback_script, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\ndss.close()\nprint('Created DSS file: ' + DSS_FILE)\n")
    print("\nWARNING: project files were generated without a DSS binary.")
    print("Run this file once in HEC-DSSVue before running HEC-HMS:")
    print("  %s" % fallback_script)

print("\n--- Summary ---")
print("Storms written: %d" % len(written))
print("DSS file: %s" % DSS_PATH)
print("Storm type: %s | Return period: %d-yr" % (STORM_TYPE, RETURN_PERIOD))
print("Subbasins assigned: %d" % len(subbasin_names))
print("\nPROVENANCE: depths from NOAA Atlas 14 Vol.1 Semiarid SW, partial-duration series")
print("PRE-SEAL: confirm storm type against RFP #660.")

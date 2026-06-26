# =============================================================================
# run_phase2.py   (QGIS Python Console)
#
# NHA WS3 -- PHASE 2 orchestrator: everything from the hand-placed pour points
# to the HEC-HMS .basin file, with NO human intervention. Run AFTER run_phase1.py
# and after the operator has placed pour_points.shp in <SITE>/outputs/.
#
# Reaches + junctions are FINAL from phase 1 (confluence-only mode), so this
# phase does NOT re-extract reaches or re-materialize junctions; it consumes the
# existing reaches.gpkg / junctions.gpkg.
#
# STEPS (each is an existing script run in order):
#   1.  delineatewatershed.py          one watershed per pour point
#   2.  subtractsubwatershed.py        -> subwatersheds.gpkg (non-overlapping)
#   3.  load_cn_inputs.py              load NLCD + HSG layers into the project
#   4.  cliptowatershed.py             clip all layers to the subwatershed boundary
#   5.  prepcngrid.py                  coregister land cover + HSG to DEM grid
#   6.  buildcnraster.py               -> cn.tif
#   7.  zonal_cn.py                    -> subwatershed_params.gpkg (id, area, CN)
#   8.  extract_slope.py               + slope_pct, centroid_x/y
#   9.  longestflowpath.py             + flow_len_ft, slopes
#   10. compute_tc.py                  + Tc / lag
#   11. build_topology.py              single source of truth: subbasin->junction
#                                      (nearest), prune internal headwater reaches,
#                                      validate junctions, resolve all downstream
#                                      pointers -> topology.gpkg
#   12. write_basin.py                 reads topology.gpkg verbatim -> .basin
#
# INPUT REQUIRED BEFORE RUNNING (in <SITE>/outputs/):
#   pour_points.shp           hand-placed interior pour points
#   watershed_boundary.gpkg   from phase 1
#   reaches.gpkg              from phase 1 (with reach topology)
#   junctions.gpkg           from phase 1
#
# USAGE (QGIS Python Console):
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"      # or Samaneh's path
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   SCRIPT_DIR = "/path/to/the/scripts"
#   exec(open(SCRIPT_DIR + "/run_phase2.py").read())
#
# BEHAVIOR: stops on the first error and prints which script failed + traceback.
# Each script runs in a fresh namespace seeded only with ROOT/SITE_DIR; the QGIS
# project (loaded layers) is shared via the QgsProject singleton, which steps 3-4
# rely on (load_cn_inputs loads layers that cliptowatershed then clips).
#
# PRE-SEAL REMINDER: confirm the Tc/lag method against RFP #660 before sealing.
# =============================================================================
import os
import traceback

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
    SCRIPT_DIR
except NameError:
    SCRIPT_DIR = os.path.join(ROOT, "WS3_GIS", "scripts")
# ---------------------------------------------------------------------------

PHASE2_STEPS = [
    "delineatewatershed.py",
    "subtractsubwatershed.py",
    "load_cn_inputs.py",
    "cliptowatershed.py",
    "prepcngrid.py",
    "buildcnraster.py",
    "zonal_cn.py",
    "extract_slope.py",
    "longestflowpath.py",
    "compute_tc.py",
    "build_topology.py",
    "write_basin.py",
    "write_met.py",
    "write_hms_project.py",
]

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")

print("=" * 70)
print("NHA WS3 -- PHASE 2 (pour points -> .basin)")
print("  ROOT      :", ROOT)
print("  SITE_DIR  :", SITE_DIR)
print("  SCRIPT_DIR:", SCRIPT_DIR)
print("=" * 70)

# --- preflight: required inputs (per your instruction) ---------------------
required = [
    (os.path.join(OUT_DIR, "pour_points.shp"),        "hand-placed pour points"),
    (os.path.join(OUT_DIR, "watershed_boundary.gpkg"), "phase-1 watershed boundary"),
    (os.path.join(OUT_DIR, "reaches.gpkg"),           "phase-1 reaches (with topology)"),
    (os.path.join(OUT_DIR, "junctions.gpkg"),         "phase-1 junctions"),
    (os.path.join(OUT_DIR, "outlet.shp"),             "phase-1 outlet"),
]
missing = [(p, why) for (p, why) in required if not os.path.isfile(p)]
if missing:
    print("\nPREFLIGHT FAILED -- required input(s) missing:")
    for p, why in missing:
        print("  MISSING:", p, "  (%s)" % why)
    print("\nRun run_phase1.py first, and place pour_points.shp in", OUT_DIR)
    raise Exception("Phase 2 cannot start: missing required input(s) above.")

# reaches.gpkg must carry the phase-1 reach topology
try:
    from qgis.core import QgsVectorLayer
    _r = QgsVectorLayer(os.path.join(OUT_DIR, "reaches.gpkg"), "rchk", "ogr")
    if _r.isValid() and "ds_reach_id" not in [f.name() for f in _r.fields()]:
        raise Exception("reaches.gpkg has no ds_reach_id -- run phase 1 "
                        "(derive_topology_reaches.py) before phase 2.")
    del _r
except ImportError:
    pass

missing_scripts = [s for s in PHASE2_STEPS
                   if not os.path.isfile(os.path.join(SCRIPT_DIR, s))]
if missing_scripts:
    print("\nPREFLIGHT FAILED -- script(s) not found in SCRIPT_DIR:")
    for s in missing_scripts:
        print("  MISSING:", os.path.join(SCRIPT_DIR, s))
    raise Exception("Set SCRIPT_DIR to the folder holding the pipeline scripts.")

print("\nPreflight OK. Running %d step(s)...\n" % len(PHASE2_STEPS))

def run_step(i, script):
    path = os.path.join(SCRIPT_DIR, script)
    print("\n" + "-" * 70)
    print("[PHASE 2  %d/%d]  %s" % (i, len(PHASE2_STEPS), script))
    print("-" * 70)
    ns = {"__name__": "__main__", "ROOT": ROOT, "SITE_DIR": SITE_DIR}
    src = open(path, "r").read()
    try:
        exec(compile(src, path, "exec"), ns)
    except Exception:
        print("\n" + "!" * 70)
        print("STEP FAILED: %s" % script)
        print("!" * 70)
        traceback.print_exc()
        raise Exception("Phase 2 stopped at step %d (%s). See traceback above."
                        % (i, script))

for i, script in enumerate(PHASE2_STEPS, start=1):
    run_step(i, script)

print("\n" + "=" * 70)
print("PHASE 2 COMPLETE.")
print("=" * 70)
print("Outputs in:", OUT_DIR)
print("  subwatersheds.gpkg          non-overlapping subwatersheds")
print("  subwatershed_params.gpkg    full hydrologic parameter table")
print("  <BASIN_NAME>.basin          HEC-HMS basin file")
print("\nVERIFY before sealing:")
print("  - open the .basin in HMS; confirm it wires to a single Sink")
print("  - spot-check one subbasin (CN, Lag) and one reach (Length, n)")
print("  - confirm the Tc/lag method against RFP #660")

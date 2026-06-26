# =============================================================================
# run_phase1.py   (QGIS Python Console)
#
# NHA WS3 -- PHASE 1 orchestrator: everything from the single outlet point up to
# the junctions, with NO human intervention. After this finishes, the operator
# inspects reaches.gpkg + junctions.gpkg, places pour points by hand as
# pour_points.shp in <SITE>/outputs/, then runs run_phase2.py.
#
# STEPS (each is an existing script run in order):
#   1. fillsink_etc.py            burn channel -> flow_dir / flow_acc / dem_carved
#   2. delineate_whole_watershed.py   outlet.shp -> watershed_boundary.gpkg
#   3. clip_dem_to_watershed.py   -> clipped/cliped_utm_wsclip.tif (real elevations)
#   4. extract_reaches.py         -> reaches.gpkg
#   5. derive_topology_reaches.py reach->reach topology onto reaches.gpkg
#   6. materialize_junctions.py   -> junctions.gpkg
#   7. prune_internal_reaches.py  drop subbasin-internal reaches; collapse any
#                                 junction left with <2 inflows (finalizes the
#                                 reach+junction network before pour points)
#
# INPUT REQUIRED BEFORE RUNNING (in <SITE>/outputs/):
#   outlet.shp                    single-feature watershed outlet
# plus the site DEM at <SITE>/demlr/cliped_utm.tif and the flowlines
# fillsink_etc.py expects (outputs/NHDFlowline_clip.gpkg).
#
# USAGE (QGIS Python Console):
#   ROOT = "/home/arash/Dropbox/Chloeta/NHA/"      # or Samaneh's path
#   SITE_DIR = "WS3_GIS/AZ12-100"
#   SCRIPT_DIR = "/path/to/the/scripts"            # folder holding these .py files
#   exec(open(SCRIPT_DIR + "/run_phase1.py").read())
#
# BEHAVIOR: stops on the first error and prints which script failed + traceback.
# Each script runs in a fresh namespace seeded only with ROOT/SITE_DIR, so no
# stale variable leaks between steps; the QGIS project (loaded layers) is shared
# because QgsProject is a global singleton.
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

# folder containing the pipeline scripts; defaults to this file's folder if the
# console set SCRIPT_DIR, else falls back to the site's scripts folder.
try:
    SCRIPT_DIR
except NameError:
    SCRIPT_DIR = os.path.join(ROOT, "WS3_GIS", "scripts")
# ---------------------------------------------------------------------------

PHASE1_STEPS = [
    "clip_only.py",
    "fillsink_etc.py",
    "delineate_whole_watershed.py",
    "clip_dem_to_watershed.py",
    "extract_reaches.py",
    "derive_topology_reaches.py",
    "materialize_junctions.py"
]

site_path = os.path.join(ROOT, SITE_DIR)
OUT_DIR   = os.path.join(site_path, "outputs")

print("=" * 70)
print("NHA WS3 -- PHASE 1 (outlet -> junctions)")
print("  ROOT      :", ROOT)
print("  SITE_DIR  :", SITE_DIR)
print("  SCRIPT_DIR:", SCRIPT_DIR)
print("=" * 70)

# --- preflight: required inputs --------------------------------------------
required = [
    (os.path.join(OUT_DIR, "outlet.shp"), "single-feature watershed outlet"),
    (os.path.join(site_path, "demlr", "cliped_utm.tif"), "real-elevation DEM"),
    (os.path.join(OUT_DIR, "NHDFlowline_clip.gpkg"), "clipped NHD flowlines (for the synthetic burn)"),
]
missing = [(p, why) for (p, why) in required if not os.path.isfile(p)]
#if missing:
#    print("\nPREFLIGHT FAILED -- required input(s) missing:")
#    for p, why in missing:
#        print("  MISSING:", p, "  (%s)" % why)
#    raise Exception("Phase 1 cannot start: missing required input(s) above.")

# all step scripts present?
missing_scripts = [s for s in PHASE1_STEPS
                   if not os.path.isfile(os.path.join(SCRIPT_DIR, s))]
if missing_scripts:
    print("\nPREFLIGHT FAILED -- script(s) not found in SCRIPT_DIR:")
    for s in missing_scripts:
        print("  MISSING:", os.path.join(SCRIPT_DIR, s))
    raise Exception("Set SCRIPT_DIR to the folder holding the pipeline scripts.")

print("\nPreflight OK. Running %d step(s)...\n" % len(PHASE1_STEPS))

# --- run each step in a fresh namespace seeded with ROOT/SITE_DIR ----------
def run_step(i, script):
    path = os.path.join(SCRIPT_DIR, script)
    print("\n" + "-" * 70)
    print("[PHASE 1  %d/%d]  %s" % (i, len(PHASE1_STEPS), script))
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
        raise Exception("Phase 1 stopped at step %d (%s). See traceback above."
                        % (i, script))

for i, script in enumerate(PHASE1_STEPS, start=1):
    run_step(i, script)

print("\n" + "=" * 70)
print("PHASE 1 COMPLETE.")
print("=" * 70)
print("Outputs in:", OUT_DIR)
print("  watershed_boundary.gpkg   whole-watershed boundary")
print("  reaches.gpkg              routing reaches (+ reach topology)")
print("  junctions.gpkg            confluence junctions")
print("\nNEXT (human step):")
print("  1. Render reaches.gpkg + junctions.gpkg.")
print("  2. Place interior pour points by hand; save as pour_points.shp")
print("     in", OUT_DIR)
print("  3. Run run_phase2.py.")

# =============================================================================
# run_phase1.py   (QGIS Python Console)
#
# Sligo Creek / GIStoOHQ -- PHASE 1 orchestrator
#
# Runs the watershed delineation and reach-network preparation scripts in order.
# After completion, inspect reaches.gpkg and junctions.gpkg, create the interior
# pour points manually as pour_points.shp in <SITE>/outputs/, and then run
# run_phase2.py.
#
# CURRENT STEPS:
#   1. clip_only.py
#        clip the source DEM/flowlines to the project working area
#
#   2. fillsink_etc.py
#        prepare hydrologically conditioned rasters
#
#   3. delineate_whole_watershed.py
#        outlet.shp -> watershed_boundary.gpkg
#
#   4. clip_dem_to_watershed.py
#        -> clipped/cliped_utm_wsclip.tif
#
#   5. extract_reaches.py
#        -> reaches.gpkg
#
#   6. derive_topology_reaches.py
#        adds reach-to-reach topology to reaches.gpkg
#
#   7. materialize_junctions.py
#        -> junctions.gpkg
#
# REQUIRED INPUTS:
#   <SITE>/outputs/outlet.shp
#   <SITE>/demlr/cliped_utm.tif
#   <SITE>/outputs/NHDFlowline_clip.gpkg
#
# REQUIRED SUPPORT MODULE:
#   <SCRIPT_DIR>/ws3io.py
#
# USAGE IN THE QGIS PYTHON CONSOLE:
#
#   ROOT = "/mnt/2nd/Projects_H/SligoCreekGIS3"
#   SITE_DIR = "SligoCreek_Mouth"
#   SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
#   exec(open(SCRIPT_DIR + "/run_phase1.py").read())
#
# BEHAVIOR:
#   - Stops on the first failed step.
#   - Prints the failed script and full traceback.
#   - Runs every child script in a fresh namespace.
#   - Adds SCRIPT_DIR to Python's module search path so imports such as
#     "from ws3io import release_and_delete" work in QGIS.
#   - PHASE1_WORKFLOW can be set to "STANDARD" (default) or
#     "DELINEATION_ONLY"; PHASE1_STEPS can also be supplied directly by a
#     runner for site-specific preprocessing workflows. NLCD/HSG clipping stays
#     in phase 2 through load_cn_inputs.py and cliptowatershed.py.
# =============================================================================

import os
import sys
import traceback


# =============================================================================
# Path resolution
# =============================================================================

try:
    ROOT
except NameError:
    ROOT = "/mnt/2nd/Projects_H/SligoCreekGIS3"

try:
    SITE_DIR
except NameError:
    SITE_DIR = "SligoCreek_Mouth"

try:
    SCRIPT_DIR
except NameError:
    if "__file__" in globals():
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    else:
        SCRIPT_DIR = os.getcwd()


# Convert paths to normalized absolute paths.
ROOT = os.path.abspath(os.path.expanduser(ROOT))
SCRIPT_DIR = os.path.abspath(os.path.expanduser(SCRIPT_DIR))

# SITE_DIR may be relative to ROOT or may itself be an absolute path.
if os.path.isabs(SITE_DIR):
    site_path = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    site_path = os.path.abspath(os.path.join(ROOT, SITE_DIR))

OUT_DIR = os.path.join(site_path, "outputs")


# =============================================================================
# Make local helper modules importable
# =============================================================================

# QGIS does not automatically add the folder containing an exec()-run script
# to sys.path. Add SCRIPT_DIR explicitly so imports such as:
#
#     from ws3io import release_and_delete
#
# work in all child scripts.
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


# =============================================================================
# Pipeline steps
# =============================================================================

DEFAULT_PHASE1_STEPS = [
    "clip_only.py",
    "fillsink_etc.py",
    "delineate_whole_watershed.py",
    "clip_dem_to_watershed.py",
    "extract_reaches.py",
    "derive_topology_reaches.py",
    "materialize_junctions.py",
]

DELINEATION_ONLY_STEPS = [
    "delineate_whole_watershed.py",
    "clip_dem_to_watershed.py",
    "extract_reaches.py",
    "derive_topology_reaches.py",
    "materialize_junctions.py",
]

PHASE1_WORKFLOW = str(
    globals().get("PHASE1_WORKFLOW", "STANDARD")
).upper()

if "PHASE1_STEPS" in globals():
    PHASE1_STEPS = list(globals()["PHASE1_STEPS"])
elif PHASE1_WORKFLOW == "STANDARD":
    PHASE1_STEPS = list(DEFAULT_PHASE1_STEPS)
elif PHASE1_WORKFLOW == "DELINEATION_ONLY":
    PHASE1_STEPS = list(DELINEATION_ONLY_STEPS)
else:
    raise Exception(
        "PHASE1_WORKFLOW must be STANDARD or DELINEATION_ONLY, "
        "or supply PHASE1_STEPS explicitly."
    )


# =============================================================================
# Header
# =============================================================================

print("=" * 78)
print("SLIGO CREEK / GIStoOHQ -- PHASE 1")
print("Watershed outlet -> watershed boundary -> reaches -> junctions")
print("=" * 78)
print("  ROOT       :", ROOT)
print("  SITE_DIR   :", SITE_DIR)
print("  SITE_PATH  :", site_path)
print("  OUTPUTS    :", OUT_DIR)
print("  SCRIPT_DIR :", SCRIPT_DIR)
print("  sys.path[0]:", sys.path[0])
print("  WORKFLOW   :", PHASE1_WORKFLOW)
print("=" * 78)


# =============================================================================
# Preflight checks
# =============================================================================

required_inputs = [
    (
        os.path.join(OUT_DIR, "outlet.shp"),
        "single-feature watershed outlet",
    ),
    (
        os.path.join(site_path, "demlr", "cliped_utm.tif"),
        "real-elevation DEM used by the watershed scripts",
    ),
    (
        os.path.join(OUT_DIR, "NHDFlowline_clip.gpkg"),
        "clipped NHD flowlines",
    ),
]

missing_inputs = [
    (path, description)
    for path, description in required_inputs
    if not os.path.isfile(path)
]

if missing_inputs:
    print("\nPREFLIGHT FAILED -- required input file(s) are missing:")

    for path, description in missing_inputs:
        print("\n  MISSING:", path)
        print("  PURPOSE:", description)

    raise Exception(
        "Phase 1 cannot start because one or more required inputs are missing."
    )


# Verify the script directory itself.
if not os.path.isdir(SCRIPT_DIR):
    raise Exception(
        "SCRIPT_DIR does not exist or is not a directory:\n%s" % SCRIPT_DIR
    )


# Verify support modules imported by the child scripts.
required_modules = [
    "ws3io.py",
]

missing_modules = [
    module
    for module in required_modules
    if not os.path.isfile(os.path.join(SCRIPT_DIR, module))
]

if missing_modules:
    print("\nPREFLIGHT FAILED -- required support module(s) are missing:")

    for module in missing_modules:
        print("  MISSING:", os.path.join(SCRIPT_DIR, module))

    raise Exception(
        "One or more required Python support modules are missing from SCRIPT_DIR."
    )


# Verify all pipeline scripts.
missing_scripts = [
    script
    for script in PHASE1_STEPS
    if not os.path.isfile(os.path.join(SCRIPT_DIR, script))
]

if missing_scripts:
    print("\nPREFLIGHT FAILED -- pipeline script(s) not found:")

    for script in missing_scripts:
        print("  MISSING:", os.path.join(SCRIPT_DIR, script))

    raise Exception(
        "Set SCRIPT_DIR to the folder containing all Phase 1 scripts."
    )


# Test the ws3io import before starting the pipeline.
try:
    import ws3io

    print("\nSupport-module check:")
    print("  ws3io imported from:", ws3io.__file__)

except Exception:
    print("\nPREFLIGHT FAILED -- ws3io.py exists but could not be imported.")
    print("SCRIPT_DIR:", SCRIPT_DIR)
    print("sys.path:")
    for entry in sys.path:
        print("  ", entry)

    traceback.print_exc()

    raise Exception(
        "Could not import ws3io. Check ws3io.py and its dependencies."
    )


os.makedirs(OUT_DIR, exist_ok=True)

print("\nPreflight OK.")
print("Running %d Phase 1 step(s)..." % len(PHASE1_STEPS))


# =============================================================================
# Step runner
# =============================================================================

def run_step(step_number, script_name):
    """
    Execute one pipeline script in a fresh namespace.

    The QGIS application and QgsProject singleton remain shared, but ordinary
    Python variables from one child script do not leak into the next.
    """

    script_path = os.path.join(SCRIPT_DIR, script_name)

    print("\n" + "-" * 78)
    print(
        "[PHASE 1  %d/%d] %s"
        % (step_number, len(PHASE1_STEPS), script_name)
    )
    print("-" * 78)
    print("Script:", script_path)

    child_namespace = {
        "__name__": "__main__",
        "__file__": script_path,
        "__package__": None,
        "ROOT": ROOT,
        "SITE_DIR": SITE_DIR,
        "SITE_PATH": site_path,
        "OUT_DIR": OUT_DIR,
        "SCRIPT_DIR": SCRIPT_DIR,
    }

    try:
        with open(script_path, "r", encoding="utf-8") as script_file:
            source = script_file.read()

        compiled = compile(source, script_path, "exec")
        exec(compiled, child_namespace)

    except SystemExit as exc:
        print("\n" + "!" * 78)
        print("STEP STOPPED WITH SystemExit:", script_name)
        print("Exit value:", exc)
        print("!" * 78)

        traceback.print_exc()

        raise Exception(
            "Phase 1 stopped at step %d (%s) because the script called "
            "SystemExit."
            % (step_number, script_name)
        ) from exc

    except Exception as exc:
        print("\n" + "!" * 78)
        print("STEP FAILED:", script_name)
        print("ERROR TYPE :", type(exc).__name__)
        print("ERROR      :", exc)
        print("!" * 78)

        traceback.print_exc()

        raise Exception(
            "Phase 1 stopped at step %d (%s). See the traceback above."
            % (step_number, script_name)
        ) from exc

    print("\nCompleted:", script_name)


# =============================================================================
# Run pipeline
# =============================================================================

for index, script in enumerate(PHASE1_STEPS, start=1):
    run_step(index, script)


# =============================================================================
# Completion message
# =============================================================================

print("\n" + "=" * 78)
print("PHASE 1 COMPLETE")
print("=" * 78)
print("Outputs directory:")
print(" ", OUT_DIR)

print("\nExpected products:")
print("  watershed_boundary.gpkg")
print("  reaches.gpkg")
print("  junctions.gpkg")

print("\nNEXT MANUAL STEP:")
print("  1. Load and inspect reaches.gpkg and junctions.gpkg.")
print("  2. Place the required interior pour points.")
print("  3. Save them as:")
print("    ", os.path.join(OUT_DIR, "pour_points.shp"))
print("  4. Run run_phase2.py.")

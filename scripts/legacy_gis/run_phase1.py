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
#   1. delineate_whole_watershed.py
#        outlet.shp + flow_dir.tif/flow_acc.tif -> watershed_boundary.gpkg
#
#   2. clip_dem_to_watershed.py
#        watershed_boundary.gpkg + DEM -> clipped/cliped_utm_wsclip.tif
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
#   OUTLET_PATH
#   DEM_PATH
#   FLOWLINE_PATH
#   <OUT_DIR>/flow_dir.tif
#   <OUT_DIR>/flow_acc.tif
#
# REQUIRED SUPPORT MODULE:
#   <SCRIPT_DIR>/ws3io.py
#
# MINIMAL USAGE IN THE QGIS PYTHON CONSOLE:
#
#   ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
#   SITE_DIR = ""
#   SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
#   exec(open(SCRIPT_DIR + "/run_phase1.py").read())
#
# USAGE WITH EXPLICIT OPTIONS:
#
#   ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
#   SITE_DIR = ""
#   SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
#
#   DEM_PATH = (
#       "/mnt/3rd900/Projects/SligoCreek_QGIS/"
#       "demlr/cliped_utm.tif"
#   )
#
#   FLOWLINE_PATH = (
#       "/mnt/3rd900/Projects/SligoCreek_QGIS/"
#       "outputs/NHDFlowline_clip.gpkg"
#   )
#
#   FORCE = True
#   TARGET_EPSG = 26918
#
#   exec(open(SCRIPT_DIR + "/run_phase1.py").read())
#
# BEHAVIOR:
#   - Stops on the first failed step.
#   - Prints the failed script and full traceback.
#   - Runs every child script in a fresh namespace.
#   - Adds SCRIPT_DIR to Python's module search path.
#   - Passes shared paths and user options into every child script.
# =============================================================================

import os
import sys
import traceback


# =============================================================================
# RUNNER-OVERRIDABLE SETTINGS
# =============================================================================

ROOT = globals().get(
    "ROOT",
    "/mnt/3rd900/Projects/SligoCreek_QGIS",
)

SITE_DIR = globals().get("SITE_DIR", "")

SCRIPT_DIR = globals().get("SCRIPT_DIR", None)

OUT_DIR = globals().get("OUT_DIR", None)
DEM_PATH = globals().get("DEM_PATH", None)
FLOWLINE_PATH = globals().get("FLOWLINE_PATH", None)
OUTLET_PATH = globals().get("OUTLET_PATH", None)
FORCE = bool(globals().get("FORCE", True))
TARGET_EPSG = globals().get("TARGET_EPSG", None)
DRY_RUN = bool(globals().get("DRY_RUN", False))
CHILD_OPTIONS = dict(globals().get("CHILD_OPTIONS", {}))
PHASE1_STEPS = list(globals().get("PHASE1_STEPS", [
    "delineate_whole_watershed.py",
    "clip_dem_to_watershed.py",
    "extract_reaches.py",
    "derive_topology_reaches.py",
    "materialize_junctions.py",
]))

START_AT = globals().get("START_AT", None)
if START_AT:
    if START_AT not in PHASE1_STEPS:
        raise Exception("START_AT step not found: %s" % START_AT)
    PHASE1_STEPS = PHASE1_STEPS[PHASE1_STEPS.index(START_AT):]


# =============================================================================
# PATH RESOLUTION
# =============================================================================

ROOT = os.path.abspath(os.path.expanduser(ROOT))

if SCRIPT_DIR is None:
    if "__file__" in globals():
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    else:
        SCRIPT_DIR = os.getcwd()

SCRIPT_DIR = os.path.abspath(os.path.expanduser(SCRIPT_DIR))

if os.path.isabs(SITE_DIR):
    SITE_PATH = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    SITE_PATH = os.path.abspath(os.path.join(ROOT, SITE_DIR))

if OUT_DIR is None:
    OUT_DIR = os.path.join(SITE_PATH, "outputs")
else:
    OUT_DIR = os.path.abspath(os.path.expanduser(OUT_DIR))

os.makedirs(OUT_DIR, exist_ok=True)

if DEM_PATH is None:
    DEM_PATH = os.path.join(SITE_PATH, "demlr", "cliped_utm.tif")
else:
    DEM_PATH = os.path.abspath(os.path.expanduser(DEM_PATH))

if FLOWLINE_PATH is None:
    FLOWLINE_PATH = os.path.join(OUT_DIR, "NHDFlowline_clip.gpkg")
else:
    FLOWLINE_PATH = os.path.abspath(os.path.expanduser(FLOWLINE_PATH))

if OUTLET_PATH is None:
    OUTLET_PATH = os.path.join(OUT_DIR, "outlet.shp")
else:
    OUTLET_PATH = os.path.abspath(os.path.expanduser(OUTLET_PATH))

FLOWDIR_PATH = os.path.join(OUT_DIR, "flow_dir.tif")
FLOWACC_PATH = os.path.join(OUT_DIR, "flow_acc.tif")


# =============================================================================
# VALIDATE SCRIPT DIRECTORY BEFORE USING IT
# =============================================================================

if not os.path.isdir(SCRIPT_DIR):
    raise Exception(
        "SCRIPT_DIR does not exist or is not a directory:\n%s"
        % SCRIPT_DIR
    )

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


# =============================================================================
# HELPERS
# =============================================================================

def shapefile_complete(shp_path):
    """Return True only when minimum required Shapefile parts exist."""
    base, extension = os.path.splitext(shp_path)

    if extension.lower() != ".shp":
        return os.path.isfile(shp_path)

    required = [
        base + ".shp",
        base + ".shx",
        base + ".dbf",
    ]

    return all(os.path.isfile(path) for path in required)


def input_exists(path):
    """Test regular files and Shapefiles safely."""
    if path.lower().endswith(".shp"):
        return shapefile_complete(path)

    return os.path.isfile(path)


def print_path_block(label, path):
    print(f"  {label:<13}: {path}")


# =============================================================================
# HEADER
# =============================================================================

print("=" * 78)
print("SLIGO CREEK / GIStoOHQ -- PHASE 1")
print("Watershed outlet -> watershed boundary -> reaches -> junctions")
print("=" * 78)

print_path_block("ROOT", ROOT)
print_path_block("SITE_DIR", SITE_DIR)
print_path_block("SITE_PATH", SITE_PATH)
print_path_block("OUT_DIR", OUT_DIR)
print_path_block("SCRIPT_DIR", SCRIPT_DIR)
print_path_block("DEM_PATH", DEM_PATH)
print_path_block("OUTLET_PATH", OUTLET_PATH)
print_path_block("FLOWLINE", FLOWLINE_PATH)
print_path_block("FLOW_DIR", FLOWDIR_PATH)
print_path_block("FLOW_ACC", FLOWACC_PATH)

print("  FORCE        :", FORCE)
print("  TARGET_EPSG  :", TARGET_EPSG)
print("  DRY_RUN      :", DRY_RUN)
print("  sys.path[0]  :", sys.path[0])
print("=" * 78)


# =============================================================================
# PREFLIGHT CHECKS
# =============================================================================

required_inputs = [
    (OUTLET_PATH, "single-feature watershed outlet"),
    (DEM_PATH, "real-elevation DEM used by watershed scripts"),
    (FLOWLINE_PATH, "clipped NHD flowlines"),
    (FLOWDIR_PATH, "flow-direction raster from hydrology preprocessing"),
    (FLOWACC_PATH, "flow-accumulation raster from hydrology preprocessing"),
]

missing_inputs = [
    (path, description)
    for path, description in required_inputs
    if not input_exists(path)
]

if missing_inputs:
    print("\nPREFLIGHT FAILED -- required input file(s) are missing:")

    for path, description in missing_inputs:
        print("\n  MISSING:", path)
        print("  PURPOSE:", description)

        if path.lower().endswith(".shp"):
            base, _ = os.path.splitext(path)
            print("  REQUIRED COMPONENTS:")
            print("   ", base + ".shp")
            print("   ", base + ".shx")
            print("   ", base + ".dbf")

    raise Exception(
        "Phase 1 cannot start because one or more required inputs are missing."
    )

required_modules = ["ws3io.py"]

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
        "One or more required Python support modules are missing."
    )

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

print("\nPreflight OK.")
print("Running %d Phase 1 step(s)..." % len(PHASE1_STEPS))

if DRY_RUN:
    print("\nDRY_RUN=True: stopping after preflight.")
    raise SystemExit(0)


# =============================================================================
# SHARED CHILD NAMESPACE
# =============================================================================

SHARED_CHILD_VARIABLES = {
    "ROOT": ROOT,
    "SITE_DIR": SITE_DIR,
    "SITE_PATH": SITE_PATH,
    "OUT_DIR": OUT_DIR,
    "SCRIPT_DIR": SCRIPT_DIR,
    "DEM_PATH": DEM_PATH,
    "OUTLET_PATH": OUTLET_PATH,
    "FLOWLINE_PATH": FLOWLINE_PATH,
    "FLOWDIR_PATH": FLOWDIR_PATH,
    "FLOWACC_PATH": FLOWACC_PATH,
    "FORCE": FORCE,
    "TARGET_EPSG": TARGET_EPSG,
    "DRY_RUN": DRY_RUN,
}

SHARED_CHILD_VARIABLES.update(CHILD_OPTIONS)


# =============================================================================
# STEP RUNNER
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
    }

    child_namespace.update(SHARED_CHILD_VARIABLES)

    try:
        with open(script_path, "r", encoding="utf-8") as script_file:
            source = script_file.read()

        compiled = compile(source, script_path, "exec")
        exec(compiled, child_namespace)

    except SystemExit as exc:
        exit_code = exc.code

        if exit_code in (None, 0):
            print("\nCompleted with SystemExit(0):", script_name)
            return

        print("\n" + "!" * 78)
        print("STEP STOPPED WITH SystemExit:", script_name)
        print("Exit value:", exit_code)
        print("!" * 78)

        traceback.print_exc()

        raise Exception(
            "Phase 1 stopped at step %d (%s) with exit code %s."
            % (step_number, script_name, exit_code)
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
# RUN PIPELINE
# =============================================================================

for index, script_name in enumerate(PHASE1_STEPS, start=1):
    run_step(index, script_name)


# =============================================================================
# COMPLETION MESSAGE
# =============================================================================

print("\n" + "=" * 78)
print("PHASE 1 COMPLETE")
print("=" * 78)
print("Outputs directory:")
print(" ", OUT_DIR)

print("\nExpected products:")
print(" ", os.path.join(OUT_DIR, "watershed_boundary.gpkg"))
print(" ", os.path.join(OUT_DIR, "reaches.gpkg"))
print(" ", os.path.join(OUT_DIR, "junctions.gpkg"))

print("\nExpected watershed-clipped DEM:")
print(" ", os.path.join(OUT_DIR, "clipped", "cliped_utm_wsclip.tif"))

print("\nNEXT MANUAL STEP:")
print("  1. Load and inspect reaches.gpkg and junctions.gpkg.")
print("  2. Place the required interior pour points.")
print("  3. Save them as:")
print("    ", os.path.join(OUT_DIR, "pour_points.shp"))
print("  4. Run run_phase2.py.")

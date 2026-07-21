# =============================================================================
# run_phase2.py   (QGIS Python Console)
#
# SLIGO CREEK / GIStoOHQ -- PHASE 2 orchestrator
#
# Runs everything from the hand-placed interior pour points through hydrologic
# parameter generation, network topology, and model-project output.
#
# Run AFTER:
#   1. run_phase1.py has completed successfully;
#   2. reaches.gpkg and junctions.gpkg have been visually verified; and
#   3. the operator has created outputs/pour_points.shp.
#
# Reaches and junctions are final products from Phase 1. This phase does not
# re-extract reaches or re-materialize junctions.
#
# STEPS
#   1.  delineatewatershed.py
#   2.  subtractsubwatershed.py
#   3.  load_cn_inputs.py
#   4.  cliptowatershed.py
#   5.  prepcngrid.py
#   6.  buildcnraster.py
#   7.  zonal_cn.py
#   8.  extract_slope.py
#   9.  longestflowpath.py
#   10. compute_tc.py
#   11. build_topology.py
#   12. write_basin.py
#   13. write_met.py
#   14. write_hms_project.py
#
# REQUIRED INPUTS IN <OUT_DIR>
#   pour_points.shp
#   watershed_boundary.gpkg
#   reaches.gpkg
#   junctions.gpkg
#   outlet.shp
#
# USAGE -- QGIS Python Console
#
#   ROOT = "/mnt/3rd900/Projects/SligoCreek_QGIS"
#   SITE_DIR = ""
#   SCRIPT_DIR = "/mnt/3rd900/Projects/PythonScripts"
#   OUT_DIR = "/mnt/3rd900/Projects/SligoCreek_QGIS/outputs"
#   FORCE = True
#   TARGET_EPSG = 26918
#   exec(open(SCRIPT_DIR + "/run_phase2.py").read())
#
# BEHAVIOR
#   - Performs a strict preflight before running any processing step.
#   - Stops on the first failed step and prints the full traceback.
#   - Runs every child script in a fresh namespace.
#   - Passes the shared project paths/settings to every child script.
#   - Shares the active QgsProject singleton, so load_cn_inputs.py can load
#     layers that cliptowatershed.py consumes afterward.
#
# NOTE
#   The current final writers are the existing HEC-HMS writers. They are kept
#   here unchanged until the GIStoOHQ writer replaces them in the pipeline.
# =============================================================================

import importlib.util
import os
import sys
import traceback

from qgis.core import QgsVectorLayer


# =============================================================================
# ROOT / PROJECT SETTINGS
# =============================================================================

ROOT = globals().get(
    "ROOT",
    "/mnt/3rd900/Projects/SligoCreek_QGIS",
)
SITE_DIR = globals().get("SITE_DIR", "")
SCRIPT_DIR = globals().get("SCRIPT_DIR", None)
OUT_DIR = globals().get("OUT_DIR", None)
FORCE = bool(globals().get("FORCE", True))
TARGET_EPSG = globals().get("TARGET_EPSG", None)
DRY_RUN = bool(globals().get("DRY_RUN", False))
CHILD_OPTIONS = dict(globals().get("CHILD_OPTIONS", {}))

PHASE2_STEPS = list(globals().get("PHASE2_STEPS", [
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
]))

START_AT = globals().get("START_AT", None)
if START_AT:
    if START_AT not in PHASE2_STEPS:
        raise Exception("START_AT step not found: %s" % START_AT)
    PHASE2_STEPS = PHASE2_STEPS[PHASE2_STEPS.index(START_AT):]


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
FAILED_STEP_MARKER = os.path.join(OUT_DIR, ".phase2_failed_step")

POUR_POINTS_PATH = globals().get(
    "POUR_POINTS_PATH",
    os.path.join(OUT_DIR, "pour_points.shp"),
)
WATERSHED_PATH = globals().get(
    "WATERSHED_PATH",
    os.path.join(OUT_DIR, "watershed_boundary.gpkg"),
)
REACHES_PATH = globals().get(
    "REACHES_PATH",
    os.path.join(OUT_DIR, "reaches.gpkg"),
)
JUNCTIONS_PATH = globals().get(
    "JUNCTIONS_PATH",
    os.path.join(OUT_DIR, "junctions.gpkg"),
)
OUTLET_PATH = globals().get(
    "OUTLET_PATH",
    os.path.join(OUT_DIR, "outlet.shp"),
)

POUR_POINTS_PATH = os.path.abspath(os.path.expanduser(POUR_POINTS_PATH))
WATERSHED_PATH = os.path.abspath(os.path.expanduser(WATERSHED_PATH))
REACHES_PATH = os.path.abspath(os.path.expanduser(REACHES_PATH))
JUNCTIONS_PATH = os.path.abspath(os.path.expanduser(JUNCTIONS_PATH))
OUTLET_PATH = os.path.abspath(os.path.expanduser(OUTLET_PATH))

if not os.path.isdir(SCRIPT_DIR):
    raise Exception(
        "SCRIPT_DIR does not exist or is not a directory:\n%s"
        % SCRIPT_DIR
    )

if SCRIPT_DIR in sys.path:
    sys.path.remove(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)


# =============================================================================
# HELPERS
# =============================================================================

def shapefile_components(path):
    """Return the minimum required components for a shapefile."""
    stem, extension = os.path.splitext(path)
    if extension.lower() != ".shp":
        return [path]

    return [
        stem + ".shp",
        stem + ".shx",
        stem + ".dbf",
    ]


def missing_components(path):
    """Return missing physical components for a required input."""
    return [
        component
        for component in shapefile_components(path)
        if not os.path.isfile(component)
    ]


def print_required_failure(path, purpose, missing):
    print("")
    print("  MISSING INPUT :", path)
    print("  PURPOSE       :", purpose)

    if len(missing) > 1 or missing[0] != path:
        print("  REQUIRED COMPONENTS:")
        for component in shapefile_components(path):
            marker = "MISSING" if component in missing else "OK"
            print("    %-8s %s" % (marker + ":", component))


def open_vector(path, name):
    """Open a vector dataset using the normal OGR provider path."""
    return QgsVectorLayer(path, name, "ogr")


def vector_field_names(layer):
    return [field.name() for field in layer.fields()]


def validate_vector(path, name, minimum_features=1):
    """Validate a Phase 1 vector product and return the loaded layer."""
    layer = open_vector(path, name)

    if not layer.isValid():
        raise Exception("Invalid or unreadable vector dataset: " + path)

    count = layer.featureCount()
    if count < minimum_features:
        raise Exception(
            "%s contains %d feature(s); at least %d required: %s"
            % (name, count, minimum_features, path)
        )

    return layer


def child_namespace(script_path):
    """Variables provided consistently to every Phase 2 child script."""
    namespace = {
        "__name__": "__main__",
        "__file__": script_path,
        "__package__": None,
        "ROOT": ROOT,
        "SITE_DIR": SITE_DIR,
        "SITE_PATH": SITE_PATH,
        "SCRIPT_DIR": SCRIPT_DIR,
        "OUT_DIR": OUT_DIR,
        "FORCE": FORCE,
        "TARGET_EPSG": TARGET_EPSG,
        "DRY_RUN": DRY_RUN,
        "POUR_POINTS_PATH": POUR_POINTS_PATH,
        "WATERSHED_PATH": WATERSHED_PATH,
        "REACHES_PATH": REACHES_PATH,
        "JUNCTIONS_PATH": JUNCTIONS_PATH,
        "OUTLET_PATH": OUTLET_PATH,
    }
    namespace.update(CHILD_OPTIONS)
    return namespace


# =============================================================================
# HEADER
# =============================================================================

print("=" * 78)
print("SLIGO CREEK / GIStoOHQ -- PHASE 2")
print("Pour points -> subwatersheds -> parameters -> topology -> model files")
print("=" * 78)
print("  ROOT         :", ROOT)
print("  SITE_DIR     :", SITE_DIR)
print("  SITE_PATH    :", SITE_PATH)
print("  OUT_DIR      :", OUT_DIR)
print("  SCRIPT_DIR   :", SCRIPT_DIR)
print("  FORCE        :", FORCE)
print("  TARGET_EPSG  :", TARGET_EPSG)
print("  DRY_RUN      :", DRY_RUN)
print("  sys.path[0]  :", sys.path[0])
print("=" * 78)


# =============================================================================
# PREFLIGHT -- REQUIRED INPUT FILES
# =============================================================================

required_inputs = [
    (
        POUR_POINTS_PATH,
        "hand-placed interior pour points",
    ),
    (
        WATERSHED_PATH,
        "Phase 1 whole-watershed boundary",
    ),
    (
        REACHES_PATH,
        "Phase 1 reaches with downstream topology",
    ),
    (
        JUNCTIONS_PATH,
        "Phase 1 materialized confluence junctions",
    ),
    (
        OUTLET_PATH,
        "Phase 1 watershed outlet",
    ),
]

input_failures = []

for path, purpose in required_inputs:
    missing = missing_components(path)
    if missing:
        input_failures.append((path, purpose, missing))

if input_failures:
    print("\nPREFLIGHT FAILED -- required input file(s) are missing:")

    for path, purpose, missing in input_failures:
        print_required_failure(path, purpose, missing)

    print("")
    print("Run run_phase1.py successfully, verify its outputs, and create:")
    print(" ", POUR_POINTS_PATH)
    raise Exception(
        "Phase 2 cannot start because one or more required inputs are missing."
    )


# =============================================================================
# PREFLIGHT -- VECTOR CONTENT / TOPOLOGY
# =============================================================================

watershed_layer = validate_vector(
    WATERSHED_PATH,
    "watershed_boundary.gpkg",
    minimum_features=1,
)

reaches_layer = validate_vector(
    REACHES_PATH,
    "reaches.gpkg",
    minimum_features=1,
)

junctions_layer = validate_vector(
    JUNCTIONS_PATH,
    "junctions.gpkg",
    minimum_features=1,
)

pour_points_layer = validate_vector(
    POUR_POINTS_PATH,
    "pour_points.shp",
    minimum_features=1,
)

outlet_layer = validate_vector(
    OUTLET_PATH,
    "outlet.shp",
    minimum_features=1,
)

reach_fields = vector_field_names(reaches_layer)
required_reach_fields = [
    "reach_id",
    "ds_type",
    "ds_reach_id",
]

missing_reach_fields = [
    field_name
    for field_name in required_reach_fields
    if field_name not in reach_fields
]

if missing_reach_fields:
    raise Exception(
        "reaches.gpkg is missing Phase 1 topology field(s): %s\n"
        "Run derive_topology_reaches.py through run_phase1.py first."
        % ", ".join(missing_reach_fields)
    )

print("\nVector-product check:")
print(
    "  watershed_boundary.gpkg :",
    watershed_layer.featureCount(),
    "feature(s)",
)
print(
    "  reaches.gpkg             :",
    reaches_layer.featureCount(),
    "feature(s)",
)
print(
    "  junctions.gpkg           :",
    junctions_layer.featureCount(),
    "feature(s)",
)
print(
    "  pour_points.shp          :",
    pour_points_layer.featureCount(),
    "feature(s)",
)
print(
    "  outlet.shp               :",
    outlet_layer.featureCount(),
    "feature(s)",
)

# The original layers are no longer needed by the orchestrator. Child scripts
# will open their own copies as needed.
del watershed_layer
del reaches_layer
del junctions_layer
del pour_points_layer
del outlet_layer


# =============================================================================
# PREFLIGHT -- PIPELINE SCRIPTS / SUPPORT MODULES
# =============================================================================

missing_scripts = [
    script
    for script in PHASE2_STEPS
    if not os.path.isfile(os.path.join(SCRIPT_DIR, script))
]

if missing_scripts:
    print("\nPREFLIGHT FAILED -- script(s) not found in SCRIPT_DIR:")

    for script in missing_scripts:
        print("  MISSING:", os.path.join(SCRIPT_DIR, script))

    raise Exception(
        "Set SCRIPT_DIR to the directory containing all Phase 2 scripts."
    )

ws3io_spec = importlib.util.find_spec("ws3io")
if ws3io_spec:
    print("\nSupport-module check:")
    print("  ws3io found at:", ws3io_spec.origin or "<unknown>")
else:
    print("\nSupport-module warning:")
    print("  ws3io could not be found during preflight.")

print("\nPreflight OK.")

if DRY_RUN:
    print("\nDRY RUN -- Phase 2 scripts that would run:")

    for index, script in enumerate(PHASE2_STEPS, start=1):
        print(
            "  %2d. %s"
            % (index, os.path.join(SCRIPT_DIR, script))
        )

    print("\nNo scripts executed because DRY_RUN=True.")

else:
    print("Running %d Phase 2 step(s)..." % len(PHASE2_STEPS))


# =============================================================================
# EXECUTION
# =============================================================================

def remember_failed_step(script):
    with open(FAILED_STEP_MARKER, "w", encoding="utf-8") as handle:
        handle.write(script + "\n")


def clear_failed_step_marker():
    if os.path.exists(FAILED_STEP_MARKER):
        os.remove(FAILED_STEP_MARKER)


def run_step(index, script):
    path = os.path.join(SCRIPT_DIR, script)

    print("\n" + "-" * 78)
    print(
        "[PHASE 2  %2d/%d] %s"
        % (index, len(PHASE2_STEPS), script)
    )
    print("-" * 78)
    print("Script:", path)

    namespace = child_namespace(path)

    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()

        exec(
            compile(source, path, "exec"),
            namespace,
        )

    except SystemExit as exc:
        if exc.code in (None, 0):
            print("\nCompleted with SystemExit(0):", script)
            return

        print("\n" + "!" * 78)
        print("STEP FAILED:", script)
        print("ERROR TYPE : SystemExit")
        print("ERROR      :", exc)
        print("!" * 78)
        remember_failed_step(script)
        raise Exception(
            "Phase 2 stopped at step %d (%s)."
            % (index, script)
        ) from exc

    except Exception as exc:
        print("\n" + "!" * 78)
        print("STEP FAILED:", script)
        print("ERROR TYPE :", type(exc).__name__)
        print("ERROR      :", exc)
        print("!" * 78)
        traceback.print_exc()
        remember_failed_step(script)

        raise Exception(
            "Phase 2 stopped at step %d (%s). See the traceback above."
            % (index, script)
        ) from exc

    print("\nCompleted:", script)


if not DRY_RUN:
    for step_index, step_script in enumerate(PHASE2_STEPS, start=1):
        run_step(step_index, step_script)
    clear_failed_step_marker()


# =============================================================================
# COMPLETION
# =============================================================================

if not DRY_RUN:
    print("\n" + "=" * 78)
    print("PHASE 2 COMPLETE")
    print("=" * 78)
    print("Outputs directory:")
    print(" ", OUT_DIR)
    print("")
    print("Expected products:")
    print(
        " ",
        os.path.join(OUT_DIR, "subwatersheds.gpkg"),
    )
    print(
        " ",
        os.path.join(OUT_DIR, "subwatershed_params.gpkg"),
    )
    print(
        " ",
        os.path.join(OUT_DIR, "topology.gpkg"),
    )
    print("")
    print("Current final writers also create the configured HEC-HMS files,")
    print("including the .basin, .met, and .hms project products.")
    print("")
    print("VERIFY BEFORE USING THE MODEL:")
    print("  1. Inspect subwatersheds.gpkg for complete, non-overlapping coverage.")
    print("  2. Inspect topology.gpkg and confirm all elements reach one outlet.")
    print("  3. Spot-check CN, slope, flow length, Tc, and lag.")
    print("  4. Confirm the selected Tc/lag method against the governing criteria.")
    print("  5. Open the generated project and verify the network connections.")

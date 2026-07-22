# =============================================================================
# derive_topology.py   (QGIS Python Console)
#
# GIStoOHQ / Sligo Creek topology utility.
#
# IMPORTANT
#   - Uses stable reach_id attributes; provider feature IDs are never written as
#     downstream identifiers.
#   - Uses GRASS stream/next_stream attributes when available.
#   - Uses endpoint geometry only as a validated fallback.
#   - Derives snap tolerance from flow_dir.tif when available.
#   - Rejects empty geometry and handles multipart lines by using the longest
#     valid part rather than flattening disconnected parts.
# =============================================================================

# Compatibility orchestrator.
#
# This legacy entry point now runs the maintained split topology scripts:
#   1. derive_topology_reaches.py
#   2. derive_topology_subbasins.py
#
# It requires junctions.gpkg to exist. In the current two-phase workflow,
# normally run the split scripts through run_phase1.py and run_phase2.py instead.

import os
import traceback

ROOT = globals().get("ROOT", "/home/arash/Dropbox/Chloeta/NHA/")
SITE_DIR = globals().get("SITE_DIR", "")
OUT_DIR = globals().get("OUT_DIR", None)
SCRIPT_DIR = globals().get("SCRIPT_DIR", None)
RUN_REACH_TOPOLOGY = bool(globals().get("RUN_REACH_TOPOLOGY", True))
RUN_SUBBASIN_TOPOLOGY = bool(globals().get("RUN_SUBBASIN_TOPOLOGY", True))

if SCRIPT_DIR is None:
    if "__file__" in globals():
        SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    else:
        SCRIPT_DIR = os.getcwd()

SCRIPT_DIR = os.path.abspath(os.path.expanduser(SCRIPT_DIR))

steps = []
if RUN_REACH_TOPOLOGY:
    steps.append("derive_topology_reaches.py")
if RUN_SUBBASIN_TOPOLOGY:
    steps.append("derive_topology_subbasins.py")

if not steps:
    raise Exception("No topology step selected.")

shared = dict(globals())
shared.update({
    "ROOT": ROOT,
    "SITE_DIR": SITE_DIR,
    "OUT_DIR": OUT_DIR,
    "SCRIPT_DIR": SCRIPT_DIR,
})

print("=" * 78)
print("DERIVE TOPOLOGY COMPATIBILITY ORCHESTRATOR")
print("=" * 78)
print("SCRIPT_DIR:", SCRIPT_DIR)
print("Steps     :", steps)

for index, script_name in enumerate(steps, start=1):
    script_path = os.path.join(SCRIPT_DIR, script_name)
    if not os.path.isfile(script_path):
        raise Exception("Topology script not found: " + script_path)

    print("\n" + "-" * 78)
    print("[%d/%d] %s" % (index, len(steps), script_name))
    print("-" * 78)

    namespace = {
        "__name__": "__main__",
        "__file__": script_path,
        "__package__": None,
    }
    namespace.update(shared)

    try:
        with open(script_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        exec(compile(source, script_path, "exec"), namespace)
    except Exception:
        print("\nFAILED:", script_name)
        traceback.print_exc()
        raise

print("\nTopology derivation complete.")

# =============================================================================
# audit_spatial_reference.py  (QGIS Python Console)
#
# Scans a GIStoOHQ site and reports:
#   - missing/invalid CRS
#   - geographic layers where projected metres are required
#   - CRS mismatches against the reference raster
#   - raster grid mismatches against flow_dir.tif
#
# It does not modify data.
# =============================================================================

import os
from osgeo import gdal
from qgis.core import QgsRasterLayer, QgsVectorLayer

ROOT = globals().get("ROOT", "/mnt/3rd900/Projects/SligoCreek_QGIS")
SITE_DIR = globals().get("SITE_DIR", "")
OUT_DIR = globals().get("OUT_DIR", None)
REFERENCE_RASTER = globals().get("REFERENCE_RASTER", None)
FAIL_ON_PROBLEM = bool(globals().get("FAIL_ON_PROBLEM", True))

ROOT = os.path.abspath(os.path.expanduser(ROOT))
if os.path.isabs(SITE_DIR):
    SITE_PATH = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    SITE_PATH = os.path.abspath(os.path.join(ROOT, SITE_DIR))
OUT_DIR = os.path.abspath(
    os.path.expanduser(OUT_DIR or os.path.join(SITE_PATH, "outputs"))
)

if REFERENCE_RASTER is None:
    candidates = [
        os.path.join(OUT_DIR, "flow_dir.tif"),
        os.path.join(OUT_DIR, "clipped", "cliped_utm_wsclip.tif"),
    ]
    REFERENCE_RASTER = next(
        (path for path in candidates if os.path.isfile(path)),
        candidates[0],
    )
REFERENCE_RASTER = os.path.abspath(os.path.expanduser(REFERENCE_RASTER))

if not os.path.isfile(REFERENCE_RASTER):
    raise Exception("Reference raster not found: " + REFERENCE_RASTER)

ref_layer = QgsRasterLayer(REFERENCE_RASTER, "reference_crs")
if not ref_layer.isValid() or not ref_layer.crs().isValid():
    raise Exception("Reference raster is invalid or has no CRS")

REF_CRS = ref_layer.crs()
if REF_CRS.isGeographic():
    raise Exception(
        "Reference raster uses geographic CRS %s; projected metres are required"
        % REF_CRS.authid()
    )


def raster_signature(path):
    ds = gdal.Open(path)
    if ds is None:
        return None
    gt = ds.GetGeoTransform()
    result = {
        "width": ds.RasterXSize,
        "height": ds.RasterYSize,
        "gt": tuple(gt),
        "projection": ds.GetProjection(),
    }
    ds = None
    return result


def same_grid(a, b):
    if a is None or b is None:
        return False
    return (
        a["width"] == b["width"]
        and a["height"] == b["height"]
        and a["projection"] == b["projection"]
        and all(abs(x - y) <= 1.0e-8 for x, y in zip(a["gt"], b["gt"]))
    )


reference_grid = raster_signature(REFERENCE_RASTER)
problems = []
rows = []

raster_extensions = {".tif", ".tiff", ".img", ".vrt"}
vector_extensions = {".gpkg", ".shp", ".geojson"}

for root, _dirs, names in os.walk(SITE_PATH):
    for name in sorted(names):
        path = os.path.join(root, name)
        ext = os.path.splitext(name)[1].lower()
        rel = os.path.relpath(path, SITE_PATH)

        if ext in raster_extensions:
            layer = QgsRasterLayer(path, name)
            if not layer.isValid():
                rows.append(("RASTER", "INVALID", "NONE", "-", rel))
                problems.append("invalid raster: " + rel)
                continue
            crs = layer.crs()
            authid = crs.authid() if crs.isValid() else "NONE"
            status = "OK"
            notes = []
            if not crs.isValid():
                status = "PROBLEM"
                notes.append("missing CRS")
            elif crs.isGeographic():
                status = "PROBLEM"
                notes.append("geographic CRS")
            elif crs != REF_CRS:
                status = "PROBLEM"
                notes.append("CRS differs from reference")

            grid_text = "-"
            # Grid equality is expected for routing rasters in outputs root.
            if os.path.dirname(path) == OUT_DIR and name in {
                "flow_dir.tif",
                "flow_acc.tif",
                "dem_carved.tif",
            }:
                sig = raster_signature(path)
                if same_grid(reference_grid, sig):
                    grid_text = "MATCH"
                else:
                    grid_text = "MISMATCH"
                    status = "PROBLEM"
                    notes.append("routing grid mismatch")

            rows.append(
                ("RASTER", status, authid, grid_text, rel)
            )
            if status == "PROBLEM":
                problems.append("%s: %s" % (rel, ", ".join(notes)))

        elif ext in vector_extensions:
            source = path
            layer = QgsVectorLayer(source, name, "ogr")
            if not layer.isValid():
                # A multi-layer GPKG may need a layer name, but opening the
                # datasource normally still resolves its first layer.
                rows.append(("VECTOR", "INVALID", "NONE", "-", rel))
                problems.append("invalid vector: " + rel)
                continue
            crs = layer.crs()
            authid = crs.authid() if crs.isValid() else "NONE"
            status = "OK"
            notes = []
            if not crs.isValid():
                status = "PROBLEM"
                notes.append("missing CRS")
            elif crs.isGeographic():
                status = "PROBLEM"
                notes.append("geographic CRS")
            elif crs != REF_CRS:
                status = "PROBLEM"
                notes.append("CRS differs from reference")
            rows.append(("VECTOR", status, authid, "-", rel))
            if status == "PROBLEM":
                problems.append("%s: %s" % (rel, ", ".join(notes)))

print("=" * 110)
print("GIStoOHQ SPATIAL-REFERENCE AUDIT")
print("=" * 110)
print("Site      :", SITE_PATH)
print("Reference :", REFERENCE_RASTER)
print("CRS       :", REF_CRS.authid())
print("")
print("%-8s %-9s %-14s %-10s %s" % (
    "TYPE", "STATUS", "CRS", "GRID", "PATH"
))
print("-" * 110)
for row in rows:
    print("%-8s %-9s %-14s %-10s %s" % row)

print("")
if problems:
    print("PROBLEMS (%d)" % len(problems))
    for problem in problems:
        print("  -", problem)
    if FAIL_ON_PROBLEM:
        raise Exception(
            "Spatial-reference audit failed with %d problem(s)." % len(problems)
        )
else:
    print("OK: all scanned GIS layers use the reference projected CRS.")

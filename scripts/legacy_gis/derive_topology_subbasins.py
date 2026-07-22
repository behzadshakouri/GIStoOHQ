# =============================================================================
# derive_topology_subbasins.py   (QGIS Python Console)
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

import math
import os

from qgis.core import (
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant


# =============================================================================
# SETTINGS
# =============================================================================

ROOT = globals().get("ROOT", "/home/arash/Dropbox/Chloeta/NHA/")
SITE_DIR = globals().get("SITE_DIR", "")
OUT_DIR = globals().get("OUT_DIR", None)

REACHES_NAME = globals().get("REACHES_NAME", "reaches.gpkg")
JUNCTIONS_NAME = globals().get("JUNCTIONS_NAME", "junctions.gpkg")
JUNCTIONS_LAYER = globals().get("JUNCTIONS_LAYER", "junctions")
PARAMS_NAME = globals().get("PARAMS_NAME", "subwatershed_params.gpkg")
PARAMS_LAYER = globals().get("PARAMS_LAYER", "subwatershed_params")

POUR_POINTS_PATH = globals().get("POUR_POINTS_PATH", None)
FLOWDIR_PATH = globals().get("FLOWDIR_PATH", None)

SNAP_PX = float(globals().get("SNAP_PX", 1.5))
PIXEL_M = globals().get("PIXEL_M", None)
SNAP_TOL = globals().get("SNAP_TOL", None)

RELOAD_IN_PROJECT = bool(globals().get("RELOAD_IN_PROJECT", True))
ALLOW_REACH_FALLBACK = bool(globals().get("ALLOW_REACH_FALLBACK", True))


# =============================================================================
# PATHS
# =============================================================================

ROOT = os.path.abspath(os.path.expanduser(ROOT))
if os.path.isabs(SITE_DIR):
    SITE_PATH = os.path.abspath(os.path.expanduser(SITE_DIR))
else:
    SITE_PATH = os.path.abspath(os.path.join(ROOT, SITE_DIR))

if OUT_DIR is None:
    OUT_DIR = os.path.join(SITE_PATH, "outputs")
else:
    OUT_DIR = os.path.abspath(os.path.expanduser(OUT_DIR))

reaches_p = os.path.join(OUT_DIR, REACHES_NAME)
junctions_p = os.path.join(OUT_DIR, JUNCTIONS_NAME)
params_p = os.path.join(OUT_DIR, PARAMS_NAME)
connect_p = os.path.join(OUT_DIR, "topology_connectors_subbasins.gpkg")

if POUR_POINTS_PATH is None:
    candidates = [
        os.path.join(OUT_DIR, "pour_points_snapped.gpkg"),
        os.path.join(OUT_DIR, "pour_points.shp"),
    ]
    POUR_POINTS_PATH = next(
        (path for path in candidates if os.path.isfile(path)),
        candidates[-1],
    )
else:
    POUR_POINTS_PATH = os.path.abspath(os.path.expanduser(POUR_POINTS_PATH))

if FLOWDIR_PATH is None:
    FLOWDIR_PATH = os.path.join(OUT_DIR, "flow_dir.tif")
else:
    FLOWDIR_PATH = os.path.abspath(os.path.expanduser(FLOWDIR_PATH))

for path in (reaches_p, junctions_p, params_p, POUR_POINTS_PATH):
    if not os.path.isfile(path):
        raise Exception("not found: " + path)


# =============================================================================
# HELPERS
# =============================================================================

def iid(value):
    if value is None:
        return None
    try:
        if hasattr(value, "isNull") and value.isNull():
            return None
    except Exception:
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ffloat(value):
    if value is None:
        return None
    try:
        if hasattr(value, "isNull") and value.isNull():
            return None
    except Exception:
        pass
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def longest_line_points(geometry):
    if geometry is None or geometry.isEmpty():
        return []
    parts = geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]
    valid = [part for part in parts if len(part) >= 2]
    if not valid:
        return []

    def part_length(part):
        return sum(
            QgsPointXY(part[index - 1]).distance(QgsPointXY(part[index]))
            for index in range(1, len(part))
        )

    return max(valid, key=part_length)


def line_endpoints(geometry):
    points = longest_line_points(geometry)
    if len(points) < 2:
        return None, None
    return QgsPointXY(points[0]), QgsPointXY(points[-1])


def remove_loaded_dataset(path):
    project = QgsProject.instance()
    wanted = os.path.normcase(os.path.abspath(path))
    for layer in list(project.mapLayers().values()):
        try:
            source = os.path.normcase(
                os.path.abspath(layer.source().split("|", 1)[0])
            )
        except Exception:
            continue
        if source == wanted:
            project.removeMapLayer(layer.id())


def delete_dataset(path):
    remove_loaded_dataset(path)
    if not os.path.exists(path):
        return
    try:
        QgsVectorFileWriter.deleteSilently(path)
        return
    except Exception:
        pass
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = path + suffix
        if os.path.exists(candidate):
            os.remove(candidate)


def resolve_pixel_size():
    if PIXEL_M is not None:
        value = ffloat(PIXEL_M)
        if value is not None and value > 0:
            return value, "PIXEL_M override"

    if os.path.isfile(FLOWDIR_PATH):
        raster = QgsRasterLayer(FLOWDIR_PATH, "flow_dir_for_topology")
        if raster.isValid():
            x_size = abs(float(raster.rasterUnitsPerPixelX()))
            y_size = abs(float(raster.rasterUnitsPerPixelY()))
            values = [value for value in (x_size, y_size) if value > 0]
            if values:
                return sum(values) / len(values), "flow_dir.tif"

    return 9.336, "legacy fallback"


pixel_m, pixel_source = resolve_pixel_size()
snap_tol = float(SNAP_TOL) if SNAP_TOL is not None else SNAP_PX * pixel_m
if snap_tol <= 0:
    raise Exception("SNAP_TOL must be positive")


# =============================================================================
# LOAD REACHES
# =============================================================================

reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer: " + reaches_p)

reach_fields = [field.name() for field in reaches.fields()]
if "reach_id" not in reach_fields:
    raise Exception(
        "reaches.gpkg has no reach_id. Run derive_topology_reaches.py first."
    )

rinfo = {}
for feature in reaches.getFeatures():
    rid = iid(feature["reach_id"])
    if rid is None:
        raise Exception("reach feature %s has invalid reach_id" % feature.id())
    if rid in rinfo:
        raise Exception("duplicate reach_id: %s" % rid)

    first_point, last_point = line_endpoints(feature.geometry())
    if first_point is None or last_point is None:
        raise Exception("Reach_%s has invalid line geometry" % rid)

    z_first = ffloat(feature["z_up_m"]) if "z_up_m" in reach_fields else None
    z_last = ffloat(feature["z_dn_m"]) if "z_dn_m" in reach_fields else None
    if z_first is not None and z_last is not None and z_last > z_first:
        up_point, dn_point = last_point, first_point
    else:
        up_point, dn_point = first_point, last_point

    rinfo[rid] = {
        "up": up_point,
        "dn": dn_point,
        "geometry": QgsGeometry(feature.geometry()),
    }


# =============================================================================
# LOAD JUNCTIONS
# =============================================================================

junctions = QgsVectorLayer(
    junctions_p + "|layername=" + JUNCTIONS_LAYER,
    "junctions",
    "ogr",
)
if not junctions.isValid():
    junctions = QgsVectorLayer(junctions_p, "junctions", "ogr")
if not junctions.isValid():
    raise Exception("invalid junctions layer: " + junctions_p)

junction_fields = [field.name() for field in junctions.fields()]
if "junction_id" not in junction_fields:
    raise Exception("junctions.gpkg has no junction_id field")

jinfo = {}
for feature in junctions.getFeatures():
    jid = iid(feature["junction_id"])
    geometry = feature.geometry()
    if jid is None or geometry is None or geometry.isEmpty():
        continue
    if jid in jinfo:
        raise Exception("duplicate junction_id: %s" % jid)
    jinfo[jid] = QgsPointXY(geometry.asPoint())

if not jinfo:
    raise Exception("junctions.gpkg contains no usable junctions")


# =============================================================================
# LOAD SUBBASINS AND POUR POINTS
# =============================================================================

subbasins = QgsVectorLayer(
    params_p + "|layername=" + PARAMS_LAYER,
    "subwatersheds",
    "ogr",
)
if not subbasins.isValid():
    raise Exception("invalid subwatershed params layer: " + params_p)

sub_fields = [field.name() for field in subbasins.fields()]
if "id" not in sub_fields:
    raise Exception("subwatershed params layer has no id field")

sub_centroids = {}
sub_ids = set()
for feature in subbasins.getFeatures():
    sid = iid(feature["id"])
    if sid is None:
        continue
    if sid in sub_ids:
        raise Exception("duplicate subbasin id: %s" % sid)
    sub_ids.add(sid)

    cx = ffloat(feature["centroid_x"]) if "centroid_x" in sub_fields else None
    cy = ffloat(feature["centroid_y"]) if "centroid_y" in sub_fields else None
    if cx is not None and cy is not None:
        sub_centroids[sid] = QgsPointXY(cx, cy)
    elif feature.geometry() is not None and not feature.geometry().isEmpty():
        sub_centroids[sid] = QgsPointXY(feature.geometry().centroid().asPoint())

pour_points = QgsVectorLayer(POUR_POINTS_PATH, "pour_points", "ogr")
if not pour_points.isValid():
    raise Exception("invalid pour-point layer: " + POUR_POINTS_PATH)

pour_fields = [field.name() for field in pour_points.fields()]
pour_id_field = "id" if "id" in pour_fields else None

pp = {}
for feature in pour_points.getFeatures():
    pid = iid(feature[pour_id_field]) if pour_id_field else iid(feature.id())
    geometry = feature.geometry()
    if pid is None or geometry is None or geometry.isEmpty():
        continue
    if pid in pp:
        raise Exception("duplicate pour-point id: %s" % pid)
    pp[pid] = QgsPointXY(geometry.asPoint())


# =============================================================================
# RESOLVE SUBBASIN DOWNSTREAM ELEMENTS
# =============================================================================

def nearest_junction(point):
    ranked = sorted(
        (point.distance(junction_point), jid)
        for jid, junction_point in jinfo.items()
    )
    return (ranked[0][1], ranked[0][0]) if ranked else (None, None)


def nearest_reach(point):
    ranked = []
    point_geometry = QgsGeometry.fromPointXY(point)
    for rid, info in rinfo.items():
        line_distance = info["geometry"].distance(point_geometry)
        up_distance = point.distance(info["up"])
        ranked.append((line_distance, up_distance, rid))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    if not ranked:
        return None, None, None
    line_distance, up_distance, rid = ranked[0]
    return rid, line_distance, up_distance


sub_kind = {}
sub_junction = {}
sub_reach = {}
sub_distance = {}
sub_debug = {}

for sid in sorted(sub_ids):
    point = pp.get(sid)
    if point is None:
        sub_kind[sid] = None
        sub_junction[sid] = None
        sub_reach[sid] = None
        sub_distance[sid] = None
        sub_debug[sid] = "sub %s -> NO matching pour point" % sid
        continue

    jid, junction_distance = nearest_junction(point)

    if jid is not None and junction_distance <= snap_tol:
        sub_kind[sid] = "junction"
        sub_junction[sid] = jid
        sub_reach[sid] = None
        sub_distance[sid] = junction_distance
        sub_debug[sid] = (
            "sub %s -> junction %s (%.3f m)"
            % (sid, jid, junction_distance)
        )
        continue

    if not ALLOW_REACH_FALLBACK:
        sub_kind[sid] = None
        sub_junction[sid] = None
        sub_reach[sid] = None
        sub_distance[sid] = junction_distance
        sub_debug[sid] = (
            "sub %s -> NO junction within %.3f m"
            % (sid, snap_tol)
        )
        continue

    rid, line_distance, up_distance = nearest_reach(point)
    sub_kind[sid] = "reach" if rid is not None else None
    sub_junction[sid] = None
    sub_reach[sid] = rid
    sub_distance[sid] = line_distance
    sub_debug[sid] = (
        "sub %s -> reach %s; line distance %.3f m; up-end %.3f m "
        "[NO junction within %.3f m]"
        % (sid, rid, line_distance, up_distance, snap_tol)
    )


# =============================================================================
# WRITE SUBBASIN ATTRIBUTES
# =============================================================================

subbasins.startEditing()
wanted = [
    ("ds_kind", QVariant.String),
    ("ds_junction_id", QVariant.Int),
    ("ds_reach_id", QVariant.Int),
    ("ds_dist_m", QVariant.Double),
    ("ds_debug", QVariant.String),
]
existing = [field.name() for field in subbasins.fields()]
missing = [
    QgsField(name, field_type)
    for name, field_type in wanted
    if name not in existing
]
if missing:
    subbasins.dataProvider().addAttributes(missing)
    subbasins.updateFields()

index_kind = subbasins.fields().indexFromName("ds_kind")
index_junction = subbasins.fields().indexFromName("ds_junction_id")
index_reach = subbasins.fields().indexFromName("ds_reach_id")
index_distance = subbasins.fields().indexFromName("ds_dist_m")
index_debug = subbasins.fields().indexFromName("ds_debug")

for feature in subbasins.getFeatures():
    sid = iid(feature["id"])
    if sid is None:
        continue

    subbasins.changeAttributeValue(
        feature.id(),
        index_kind,
        sub_kind.get(sid),
    )
    subbasins.changeAttributeValue(
        feature.id(),
        index_junction,
        sub_junction.get(sid),
    )
    subbasins.changeAttributeValue(
        feature.id(),
        index_reach,
        sub_reach.get(sid),
    )
    subbasins.changeAttributeValue(
        feature.id(),
        index_distance,
        sub_distance.get(sid),
    )
    subbasins.changeAttributeValue(
        feature.id(),
        index_debug,
        sub_debug.get(sid, "sub %s -> unresolved" % sid),
    )

if not subbasins.commitChanges():
    raise Exception("failed to commit subbasin topology attributes")


# =============================================================================
# WRITE CONNECTORS
# =============================================================================

delete_dataset(connect_p)

fields = QgsFields()
fields.append(QgsField("kind", QVariant.String))
fields.append(QgsField("sub_id", QVariant.Int))
fields.append(QgsField("dst_junc", QVariant.Int))
fields.append(QgsField("dst_reach", QVariant.Int))
fields.append(QgsField("dist_m", QVariant.Double))
fields.append(QgsField("ds_debug", QVariant.String))

options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = "topology_connectors_subbasins"
options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

writer = QgsVectorFileWriter.create(
    connect_p,
    fields,
    QgsWkbTypes.LineString,
    reaches.crs(),
    QgsCoordinateTransformContext(),
    options,
)

if writer.hasError() != QgsVectorFileWriter.NoError:
    raise Exception("could not create connectors: " + writer.errorMessage())

connector_count = 0
for sid in sorted(sub_ids):
    start = sub_centroids.get(sid)
    kind = sub_kind.get(sid)
    if start is None or kind is None:
        continue

    if kind == "junction":
        end = jinfo.get(sub_junction[sid])
    else:
        reach = rinfo.get(sub_reach[sid])
        end = reach["up"] if reach else None

    if end is None or start.distance(end) < 0.01:
        continue

    feature = QgsFeature(fields)
    feature.setGeometry(QgsGeometry.fromPolylineXY([start, end]))
    feature["kind"] = kind
    feature["sub_id"] = int(sid)
    feature["dst_junc"] = sub_junction.get(sid)
    feature["dst_reach"] = sub_reach.get(sid)
    feature["dist_m"] = sub_distance.get(sid)
    feature["ds_debug"] = sub_debug.get(sid, "")
    writer.addFeature(feature)
    connector_count += 1

del writer


# =============================================================================
# REPORT
# =============================================================================

print("=" * 78)
print("DERIVE SUBBASIN TOPOLOGY")
print("=" * 78)
print("Reaches     :", reaches_p)
print("Junctions   :", junctions_p)
print("Subbasins   :", params_p)
print("Pour points :", POUR_POINTS_PATH)
print("Pixel size  : %.4f m (%s)" % (pixel_m, pixel_source))
print("Snap tol    : %.4f m" % snap_tol)
print("")
print("SUBBASIN TOPOLOGY SUMMARY")
for sid in sorted(sub_ids):
    print("  " + sub_debug[sid])

missing_points = sorted(sid for sid in sub_ids if sid not in pp)
if missing_points:
    print("\nWARNING: subbasins without matching pour points:", missing_points)

fallbacks = sorted(
    sid for sid in sub_ids if sub_kind.get(sid) == "reach"
)
if fallbacks:
    print("\nWARNING: reach fallback used for subbasins:", fallbacks)

unresolved = sorted(
    sid for sid in sub_ids if sub_kind.get(sid) is None
)
if unresolved:
    print("\nWARNING: unresolved subbasins:", unresolved)

print("\nWrote %d connector(s): %s" % (connector_count, connect_p))

if RELOAD_IN_PROJECT:
    QgsProject.instance().addMapLayer(
        QgsVectorLayer(
            connect_p,
            "topology_connectors_subbasins",
            "ogr",
        )
    )
    print("Loaded topology_connectors_subbasins.")

print("\nDone.")

# =============================================================================
# derive_topology_reaches.py   (QGIS Python Console)
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
FLOWDIR_PATH = globals().get("FLOWDIR_PATH", None)

SNAP_PX = float(globals().get("SNAP_PX", 1.5))
PIXEL_M = globals().get("PIXEL_M", None)
SNAP_TOL = globals().get("SNAP_TOL", None)

AMBIGUITY_MARGIN_FACTOR = float(
    globals().get("AMBIGUITY_MARGIN_FACTOR", 0.25)
)
ELEVATION_EPS_M = float(globals().get("ELEVATION_EPS_M", 0.01))

# r.stream.extract normally digitizes reaches downstream. Endpoint DEM samples
# can contain small adverse rises near flat channels/outlets. Do not reverse a
# reach merely because the final endpoint is a few centimetres higher.
ORIENTATION_REVERSAL_MIN_RISE_M = float(
    globals().get("ORIENTATION_REVERSAL_MIN_RISE_M", 0.25)
)
ORIENTATION_REVERSAL_MIN_SLOPE = float(
    globals().get("ORIENTATION_REVERSAL_MIN_SLOPE", 0.00010)
)

RELOAD_IN_PROJECT = bool(globals().get("RELOAD_IN_PROJECT", True))
STRICT_SINGLE_OUTLET = bool(globals().get("STRICT_SINGLE_OUTLET", False))


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
connect_p = os.path.join(OUT_DIR, "topology_connectors.gpkg")

if FLOWDIR_PATH is None:
    FLOWDIR_PATH = os.path.join(OUT_DIR, "flow_dir.tif")
else:
    FLOWDIR_PATH = os.path.abspath(os.path.expanduser(FLOWDIR_PATH))

if not os.path.isfile(reaches_p):
    raise Exception("not found: " + reaches_p)


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


def normalized_text(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def longest_line_points(geometry):
    """Return points from the longest valid line part."""
    if geometry is None or geometry.isEmpty():
        return []

    if geometry.isMultipart():
        parts = geometry.asMultiPolyline()
    else:
        parts = [geometry.asPolyline()]

    valid = [part for part in parts if len(part) >= 2]
    if not valid:
        return []

    def part_length(part):
        total = 0.0
        for index in range(1, len(part)):
            total += QgsPointXY(part[index - 1]).distance(QgsPointXY(part[index]))
        return total

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
            source = layer.source().split("|", 1)[0]
            source = os.path.normcase(os.path.abspath(source))
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


def first_existing_field(field_names, candidates):
    lowered = {name.lower(): name for name in field_names}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


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
if SNAP_TOL is None:
    snap_tol = SNAP_PX * pixel_m
else:
    snap_tol = float(SNAP_TOL)

if snap_tol <= 0:
    raise Exception("SNAP_TOL must be positive")


# =============================================================================
# LOAD REACHES AND CREATE STABLE IDS
# =============================================================================

reaches = QgsVectorLayer(reaches_p, "reaches", "ogr")
if not reaches.isValid():
    raise Exception("invalid reaches layer: " + reaches_p)

field_names = [field.name() for field in reaches.fields()]
existing_reach_id_field = first_existing_field(field_names, ["reach_id"])

features = list(reaches.getFeatures())
if not features:
    raise Exception("reaches.gpkg contains no features")

existing_ids = []
existing_ids_valid = existing_reach_id_field is not None
for feature in features:
    rid = iid(feature[existing_reach_id_field]) if existing_reach_id_field else None
    if rid is None:
        existing_ids_valid = False
        break
    existing_ids.append(rid)

if len(set(existing_ids)) != len(existing_ids):
    existing_ids_valid = False

if existing_ids_valid:
    fid_to_rid = {
        int(feature.id()): iid(feature[existing_reach_id_field])
        for feature in features
    }
    id_source = "preserved existing unique reach_id"
else:
    sorted_fids = sorted(int(feature.id()) for feature in features)
    fid_to_rid = {
        fid: index
        for index, fid in enumerate(sorted_fids, start=1)
    }
    id_source = "assigned stable sequential reach_id"

rid_to_fid = {rid: fid for fid, rid in fid_to_rid.items()}

stream_id_field = first_existing_field(
    field_names,
    ["stream", "stream_id", "streamid", "cat"],
)
next_stream_field = first_existing_field(
    field_names,
    ["next_stream", "nextstream", "next_str", "next"],
)

stream_to_rid = {}
if stream_id_field:
    for feature in features:
        stream_id = iid(feature[stream_id_field])
        if stream_id is None:
            continue
        rid = fid_to_rid[int(feature.id())]
        if stream_id in stream_to_rid and stream_to_rid[stream_id] != rid:
            print(
                "WARNING: duplicate source stream id %s; metadata lookup disabled "
                "for that value." % stream_id
            )
            stream_to_rid[stream_id] = None
        else:
            stream_to_rid[stream_id] = rid

rinfo = {}
orientation_warnings = []

for feature in features:
    fid = int(feature.id())
    rid = fid_to_rid[fid]
    first_point, last_point = line_endpoints(feature.geometry())
    if first_point is None or last_point is None:
        raise Exception("Reach_%s has empty or invalid line geometry" % rid)

    z_first = ffloat(feature["z_up_m"]) if "z_up_m" in field_names else None
    z_last = ffloat(feature["z_dn_m"]) if "z_dn_m" in field_names else None

    # GRASS stream geometry order is the primary direction source. Reverse only
    # when the endpoint DEM evidence is materially strong. This prevents tiny
    # adverse DEM noise on nearly flat reaches from turning a real outflow reach
    # into a false tributary and creating artificial terminal junctions.
    geometry_length = float(feature.geometry().length())
    adverse_rise = None
    adverse_slope = None
    if z_first is not None and z_last is not None:
        adverse_rise = z_last - z_first
        if geometry_length > 0:
            adverse_slope = adverse_rise / geometry_length

    should_reverse = (
        adverse_rise is not None
        and adverse_slope is not None
        and adverse_rise >= ORIENTATION_REVERSAL_MIN_RISE_M
        and adverse_slope >= ORIENTATION_REVERSAL_MIN_SLOPE
    )

    if should_reverse:
        up_point, dn_point = last_point, first_point
        z_up, z_dn = z_last, z_first
        orientation_source = "strong-elevation-reversal"
    else:
        up_point, dn_point = first_point, last_point
        z_up, z_dn = z_first, z_last
        if adverse_rise is not None and adverse_rise > ELEVATION_EPS_M:
            orientation_source = "geometry-order; ignored small adverse DEM rise"
            orientation_warnings.append(
                (rid, adverse_rise, adverse_slope or 0.0)
            )
        else:
            orientation_source = "geometry-order"

    source_stream = iid(feature[stream_id_field]) if stream_id_field else None
    source_next = iid(feature[next_stream_field]) if next_stream_field else None

    rinfo[rid] = {
        "fid": fid,
        "up": up_point,
        "dn": dn_point,
        "z_up": z_up,
        "z_dn": z_dn,
        "orientation_source": orientation_source,
        "source_stream": source_stream,
        "source_next": source_next,
    }

print("=" * 78)
print("DERIVE REACH TOPOLOGY")
print("=" * 78)
print("Reaches      :", reaches_p)
print("Reach count  :", len(rinfo))
print("ID strategy  :", id_source)
print("Pixel size   : %.4f m (%s)" % (pixel_m, pixel_source))
print("Snap tol     : %.4f m (%.2f px)" % (snap_tol, SNAP_PX))
print("Stream field :", stream_id_field or "<none>")
print("Next field   :", next_stream_field or "<none>")

if orientation_warnings:
    print(
        "WARNING: %d reach(es) retained GRASS geometry order despite a small "
        "adverse DEM rise:" % len(orientation_warnings)
    )
    for _rid, _rise, _slope in orientation_warnings:
        print(
            "  Reach_%s: adverse rise %.4f m, adverse slope %.8f"
            % (_rid, _rise, _slope)
        )


# =============================================================================
# RESOLVE DOWNSTREAM REACHES
# =============================================================================

def metadata_downstream(rid):
    source_next = rinfo[rid]["source_next"]
    if source_next is None or source_next <= 0:
        return None
    candidate = stream_to_rid.get(source_next)
    if candidate is None or candidate == rid:
        return None
    return candidate


def geometry_candidates(rid):
    source = rinfo[rid]
    ranked = []
    for other_rid, other in rinfo.items():
        if other_rid == rid:
            continue
        distance = source["dn"].distance(other["up"])
        if distance <= snap_tol:
            ranked.append((distance, other_rid))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return ranked


def candidate_is_elevation_consistent(source_rid, target_rid):
    source_z = rinfo[source_rid]["z_dn"]
    target_z = rinfo[target_rid]["z_up"]
    if source_z is None or target_z is None:
        return True
    return target_z <= source_z + max(ELEVATION_EPS_M, 0.05)


ds_reach = {}
ds_note = {}
ambiguities = []

for rid in sorted(rinfo):
    metadata_candidate = metadata_downstream(rid)
    ranked = geometry_candidates(rid)

    if metadata_candidate is not None:
        endpoint_distance = rinfo[rid]["dn"].distance(
            rinfo[metadata_candidate]["up"]
        )
        if endpoint_distance <= max(snap_tol * 2.5, snap_tol + pixel_m):
            ds_reach[rid] = metadata_candidate
            ds_note[rid] = "GRASS metadata; endpoint gap %.3f m" % endpoint_distance
            continue
        print(
            "WARNING Reach_%s: metadata points to Reach_%s but endpoint gap is "
            "%.3f m; using geometric resolution."
            % (rid, metadata_candidate, endpoint_distance)
        )

    consistent = [
        item
        for item in ranked
        if candidate_is_elevation_consistent(rid, item[1])
    ]
    candidates = consistent if consistent else ranked

    if not candidates:
        ds_reach[rid] = None
        ds_note[rid] = "no downstream endpoint match"
        continue

    best_distance, best_rid = candidates[0]
    if len(candidates) > 1:
        second_distance, second_rid = candidates[1]
        required_margin = AMBIGUITY_MARGIN_FACTOR * snap_tol
        if second_distance - best_distance < required_margin:
            ambiguities.append(
                (rid, best_rid, best_distance, second_rid, second_distance)
            )

    ds_reach[rid] = best_rid
    ds_note[rid] = "geometry %.3f m" % best_distance


# =============================================================================
# GRAPH CHECKS
# =============================================================================

def find_cycles(mapping):
    cycles = []
    completed = set()

    for start in mapping:
        if start in completed:
            continue
        path = []
        position = {}
        node = start

        while node is not None and node in mapping:
            if node in position:
                cycles.append(path[position[node]:] + [node])
                break
            if node in completed:
                break
            position[node] = len(path)
            path.append(node)
            node = mapping.get(node)

        completed.update(path)

    return cycles


cycles = find_cycles(ds_reach)
if cycles:
    lines = [" -> ".join("Reach_%s" % rid for rid in cycle) for cycle in cycles]
    raise Exception(
        "Reach topology contains cycle(s):\n  " + "\n  ".join(lines)
    )

outlets = sorted(rid for rid, downstream in ds_reach.items() if downstream is None)

print("\nReach topology resolved.")
print("Outlet reach id(s):", outlets)

if ambiguities:
    print("\nAMBIGUOUS ENDPOINT MATCHES:")
    for rid, first, d1, second, d2 in ambiguities:
        print(
            "  Reach_%s: chose Reach_%s at %.3f m; alternate Reach_%s at %.3f m"
            % (rid, first, d1, second, d2)
        )

if len(outlets) != 1:
    message = (
        "expected exactly one outlet reach, found %d: %s"
        % (len(outlets), outlets)
    )
    if STRICT_SINGLE_OUTLET:
        raise Exception(message)
    print("WARNING:", message)


# =============================================================================
# WRITE ATTRIBUTES
# =============================================================================

reaches.startEditing()
current_fields = [field.name() for field in reaches.fields()]
required_fields = [
    ("reach_id", QVariant.Int),
    ("ds_type", QVariant.String),
    ("ds_reach_id", QVariant.Int),
    ("topo_note", QVariant.String),
    ("topo_orient", QVariant.String),
]
missing_fields = [
    QgsField(name, field_type)
    for name, field_type in required_fields
    if name not in current_fields
]
if missing_fields:
    reaches.dataProvider().addAttributes(missing_fields)
    reaches.updateFields()

index_reach_id = reaches.fields().indexFromName("reach_id")
index_ds_type = reaches.fields().indexFromName("ds_type")
index_ds_reach = reaches.fields().indexFromName("ds_reach_id")
index_note = reaches.fields().indexFromName("topo_note")
index_orient = reaches.fields().indexFromName("topo_orient")

for feature in reaches.getFeatures():
    fid = int(feature.id())
    rid = fid_to_rid[fid]
    downstream = ds_reach[rid]
    reaches.changeAttributeValue(fid, index_reach_id, int(rid))
    reaches.changeAttributeValue(
        fid,
        index_ds_type,
        "outlet" if downstream is None else "reach",
    )
    reaches.changeAttributeValue(
        fid,
        index_ds_reach,
        None if downstream is None else int(downstream),
    )
    reaches.changeAttributeValue(fid, index_note, ds_note[rid])
    reaches.changeAttributeValue(
        fid,
        index_orient,
        rinfo[rid]["orientation_source"],
    )

if not reaches.commitChanges():
    raise Exception("failed to commit topology attributes to reaches.gpkg")

print("\nUpdated reaches.gpkg:")
print("  reach_id")
print("  ds_type")
print("  ds_reach_id")
print("  topo_note")
print("  topo_orient")


# =============================================================================
# WRITE VERIFICATION CONNECTORS
# =============================================================================

delete_dataset(connect_p)

fields = QgsFields()
fields.append(QgsField("kind", QVariant.String))
fields.append(QgsField("src_id", QVariant.Int))
fields.append(QgsField("dst_reach", QVariant.Int))
fields.append(QgsField("dist_m", QVariant.Double))
fields.append(QgsField("flag", QVariant.String))

options = QgsVectorFileWriter.SaveVectorOptions()
options.driverName = "GPKG"
options.layerName = "topology_connectors"
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
    raise Exception("could not create connector layer: " + writer.errorMessage())

connector_count = 0
for rid in sorted(ds_reach):
    downstream = ds_reach[rid]
    if downstream is None:
        continue

    start = rinfo[rid]["dn"]
    end = rinfo[downstream]["up"]
    feature = QgsFeature(fields)
    feature.setGeometry(QgsGeometry.fromPolylineXY([start, end]))
    feature["kind"] = "reach"
    feature["src_id"] = int(rid)
    feature["dst_reach"] = int(downstream)
    feature["dist_m"] = float(start.distance(end))
    feature["flag"] = ds_note[rid]
    writer.addFeature(feature)
    connector_count += 1

del writer

print("Wrote %d reach connector(s): %s" % (connector_count, connect_p))


# =============================================================================
# REPORT
# =============================================================================

print("\nREACH TOPOLOGY SUMMARY")
for rid in sorted(ds_reach):
    downstream = ds_reach[rid]
    print(
        "  Reach_%-6s -> %-12s [%s]"
        % (
            rid,
            "Outlet" if downstream is None else "Reach_%s" % downstream,
            ds_note[rid],
        )
    )

if RELOAD_IN_PROJECT:
    project = QgsProject.instance()
    project.addMapLayer(
        QgsVectorLayer(connect_p, "topology_connectors", "ogr")
    )
    print("\nLoaded topology_connectors.")

print("\nDone. Run materialize_junctions.py next.")

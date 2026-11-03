"""Generic ArcGIS REST (MapServer/FeatureServer) feature downloader.

This talks to any public ArcGIS Server feature service: it discovers layers
from the service's own metadata (including group-layer hierarchy), selects
layers by name or regular expression instead of hardcoded IDs, pages through
every record honoring the service's own ``maxRecordCount``, and writes the
selected layers to a GeoPackage with a provenance manifest.

Nothing here knows about any specific municipality's layer catalog or
stormwater semantics -- callers decide which layers matter and what to do
with them afterward.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..watershed_data.network import download_bytes
from ._gpkg import require_geopandas, sanitize_layer_name
from .manifest import SourceLayerRecord, build_manifest, utc_now_iso, write_manifest

# Esri's own sentinel for "not nested under a group layer".
_NO_PARENT = -1


class ArcGISRestError(ValueError):
    """Raised when an ArcGIS REST service response violates its own contract."""


@dataclass(frozen=True)
class ArcGISLayerInfo:
    """One entry from the service's own layer/table catalog."""

    layer_id: int
    name: str
    layer_type: str
    parent_layer_id: int | None
    sub_layer_ids: tuple[int, ...]
    geometry_type: str | None
    is_feature_layer: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "name": self.name,
            "layer_type": self.layer_type,
            "parent_layer_id": self.parent_layer_id,
            "sub_layer_ids": list(self.sub_layer_ids),
            "geometry_type": self.geometry_type,
            "is_feature_layer": self.is_feature_layer,
        }


@dataclass(frozen=True)
class BoundingBox:
    """A WGS84-or-other bounding box used for optional spatial clipping."""

    west: float
    south: float
    east: float
    north: float
    sr: int = 4326

    def __post_init__(self) -> None:
        values = (self.west, self.south, self.east, self.north)
        if not all(isinstance(v, (int, float)) for v in values):
            raise ArcGISRestError("Bounding box coordinates must be numeric")
        if self.west >= self.east or self.south >= self.north:
            raise ArcGISRestError(
                "Bounding box must satisfy west < east and south < north"
            )

    def as_envelope(self) -> str:
        return f"{self.west},{self.south},{self.east},{self.north}"


def _get_json(
    url: str,
    params: dict[str, Any],
    *,
    opener: Callable[..., object],
    timeout: float,
    label: str,
) -> tuple[dict[str, Any], str]:
    full_url = url + "?" + urllib.parse.urlencode(params)
    raw, _, _ = download_bytes(full_url, opener=opener, timeout=timeout, label=label)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArcGISRestError(f"{label}: response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ArcGISRestError(f"{label}: response is not a JSON object")
    error = payload.get("error")
    if isinstance(error, dict):
        raise ArcGISRestError(f"{label}: service returned an error: {error}")
    return payload, full_url


def discover_layers(
    service_url: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
) -> list[ArcGISLayerInfo]:
    """Discover every layer and table from the service's own ``?f=json`` root.

    Group layers are returned alongside their children with hierarchy intact
    (``parent_layer_id``/``sub_layer_ids``) -- nothing is flattened away or
    hardcoded by ID.
    """
    base = service_url.rstrip("/")
    payload, _ = _get_json(
        base, {"f": "json"}, opener=opener, timeout=timeout,
        label="ArcGIS service discovery",
    )
    entries: list[ArcGISLayerInfo] = []
    for collection_key, is_table in (("layers", False), ("tables", True)):
        for raw in payload.get(collection_key) or []:
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            layer_id = int(raw["id"])
            layer_type = str(raw.get("type") or ("Table" if is_table else "unknown"))
            parent = raw.get("parentLayerId")
            sub_ids = tuple(int(x) for x in (raw.get("subLayerIds") or ()))
            entries.append(ArcGISLayerInfo(
                layer_id=layer_id,
                name=str(raw.get("name") or f"layer_{layer_id}"),
                layer_type=layer_type,
                parent_layer_id=(
                    None if parent in (None, _NO_PARENT) else int(parent)
                ),
                sub_layer_ids=sub_ids,
                geometry_type=raw.get("geometryType"),
                is_feature_layer=(layer_type == "Feature Layer"),
            ))
    if not entries:
        raise ArcGISRestError(
            "ArcGIS service discovery found no layers or tables; "
            "confirm the URL points at a MapServer/FeatureServer root"
        )
    return entries


def select_layers(
    layers: Sequence[ArcGISLayerInfo],
    *,
    names: Sequence[str] | None = None,
    pattern: str | None = None,
    feature_layers_only: bool = True,
) -> list[ArcGISLayerInfo]:
    """Select discovered layers by exact name(s) or a regular expression.

    Exactly one of ``names``/``pattern`` must be given -- there is no
    hardcoded-ID path. Raises :class:`ArcGISRestError` if a requested name
    is not found or the pattern matches nothing, rather than silently
    returning an empty/partial selection.
    """
    if bool(names) == bool(pattern):
        raise ArcGISRestError("select_layers requires exactly one of names or pattern")
    candidates = [
        layer for layer in layers if not feature_layers_only or layer.is_feature_layer
    ]
    if names:
        wanted = list(names)
        by_lower_name: dict[str, list[ArcGISLayerInfo]] = {}
        for layer in candidates:
            by_lower_name.setdefault(layer.name.lower(), []).append(layer)
        selected: list[ArcGISLayerInfo] = []
        missing = []
        for name in wanted:
            matches = by_lower_name.get(name.lower())
            if not matches:
                missing.append(name)
            else:
                selected.extend(matches)
        if missing:
            raise ArcGISRestError(f"No layer matched name(s): {missing}")
        return selected
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ArcGISRestError(f"Invalid layer-name pattern {pattern!r}: {exc}") from exc
    matched = [layer for layer in candidates if compiled.search(layer.name)]
    if not matched:
        raise ArcGISRestError(f"No layer name matched pattern: {pattern!r}")
    return matched


def fetch_layer_metadata(
    service_url: str,
    layer_id: int,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Fetch a single layer's own ``?f=json`` metadata (fields, OID field, ...)."""
    url = f"{service_url.rstrip('/')}/{layer_id}"
    payload, _ = _get_json(
        url, {"f": "json"}, opener=opener, timeout=timeout,
        label=f"ArcGIS layer {layer_id} metadata",
    )
    return payload


def _object_id_field(metadata: dict[str, Any]) -> str | None:
    declared = metadata.get("objectIdField")
    if isinstance(declared, str) and declared:
        return declared
    for raw_field in metadata.get("fields") or []:
        if isinstance(raw_field, dict) and raw_field.get("type") == "esriFieldTypeOID":
            name = raw_field.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def download_layer_features(
    service_url: str,
    layer_id: int,
    *,
    metadata: dict[str, Any] | None = None,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int | None = None,
    bbox: BoundingBox | None = None,
    page_size: int | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
    max_pages: int = 10_000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page through every record of one layer as GeoJSON features.

    Honors the service's own ``maxRecordCount`` (never requests more per
    page than the server allows), orders by the object-ID field when the
    layer exposes one, and aborts rather than looping forever if a page does
    not advance past records already seen (duplicate-page protection).
    """
    if metadata is None:
        metadata = fetch_layer_metadata(
            service_url, layer_id, opener=opener, timeout=timeout
        )
    server_max = metadata.get("maxRecordCount")
    server_max = int(server_max) if isinstance(server_max, (int, float)) and server_max else 1000
    effective_page = min(page_size, server_max) if page_size else server_max
    if effective_page <= 0:
        raise ArcGISRestError(f"ArcGIS layer {layer_id} has a non-positive page size")

    object_id_field = _object_id_field(metadata)
    base_params: dict[str, Any] = {
        "where": where, "outFields": out_fields, "f": "geojson",
        "returnGeometry": "true",
    }
    if out_sr is not None:
        base_params["outSR"] = out_sr
    if object_id_field:
        base_params["orderByFields"] = f"{object_id_field} ASC"
    if bbox is not None:
        base_params.update({
            "geometry": bbox.as_envelope(),
            "geometryType": "esriGeometryEnvelope",
            "inSR": bbox.sr,
            "spatialRel": "esriSpatialRelIntersects",
        })
    query_url = f"{service_url.rstrip('/')}/{layer_id}/query"

    features: list[dict[str, Any]] = []
    seen_object_ids: set[Any] = set()
    crs: dict[str, Any] | None = None
    offset = 0
    for _ in range(max_pages):
        params = dict(base_params, resultOffset=offset, resultRecordCount=effective_page)
        payload, _ = _get_json(
            query_url, params, opener=opener, timeout=timeout,
            label=f"ArcGIS layer {layer_id} query at offset {offset}",
        )
        page = payload.get("features")
        if not isinstance(page, list):
            raise ArcGISRestError(
                f"ArcGIS layer {layer_id} query response has no features array"
            )
        if crs is None and isinstance(payload.get("crs"), dict):
            crs = payload["crs"]
        page_count = len(page)
        if page_count == 0:
            break
        if object_id_field:
            page_object_ids = {
                feature.get("properties", {}).get(object_id_field)
                for feature in page if isinstance(feature, dict)
            }
            page_object_ids.discard(None)
            if page_object_ids and page_object_ids <= seen_object_ids:
                raise ArcGISRestError(
                    f"ArcGIS layer {layer_id}: duplicate page at offset {offset} "
                    "(server did not advance past records already seen); aborting "
                    "rather than duplicating features"
                )
            seen_object_ids |= page_object_ids
        features.extend(page)
        if page_count < effective_page:
            break
        offset += page_count
    else:
        raise ArcGISRestError(
            f"ArcGIS layer {layer_id}: exceeded max_pages={max_pages} while paginating"
        )

    query_info = {
        "crs": crs,
        "base_query_url": query_url,
        "base_query_parameters": dict(base_params),
        "effective_page_size": effective_page,
        "server_max_record_count": server_max,
        "object_id_field": object_id_field,
    }
    return features, query_info


def _resolve_crs(crs_from_response: dict[str, Any] | None, out_sr: int | None) -> Any:
    if out_sr is not None:
        return f"EPSG:{out_sr}"
    if isinstance(crs_from_response, dict):
        properties = crs_from_response.get("properties") or {}
        name = properties.get("name")
        if isinstance(name, str) and name:
            # GeoJSON CRS URNs look like "urn:ogc:def:crs:EPSG::4326".
            if "EPSG" in name:
                code = name.rsplit(":", 1)[-1]
                if code.isdigit():
                    return f"EPSG:{code}"
            return name
    # ArcGIS's GeoJSON output defaults to WGS84 when no outSR is requested.
    return "EPSG:4326"


def _field_names_from_metadata(metadata: dict[str, Any]) -> list[str]:
    names = []
    for raw_field in metadata.get("fields") or []:
        if isinstance(raw_field, dict):
            name = raw_field.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def write_features_to_gpkg(
    features: Sequence[dict[str, Any]],
    output_path: str | Path,
    layer_name: str,
    *,
    metadata: dict[str, Any],
    crs_from_response: dict[str, Any] | None = None,
    out_sr: int | None = None,
) -> Path:
    """Write GeoJSON-shaped features to one GeoPackage layer.

    Attribute values -- including ``None`` -- are passed through exactly as
    received; nothing here fabricates or substitutes a default. An empty
    feature list still writes a layer with the schema derived from the
    layer's own field metadata, so a layer with zero matching records is not
    silently skipped from the output.
    """
    gpd = require_geopandas()
    from shapely.geometry import shape as shapely_shape

    crs = _resolve_crs(crs_from_response, out_sr)
    if features:
        rows: list[dict[str, Any]] = []
        geometries = []
        for feature in features:
            if not isinstance(feature, dict):
                raise ArcGISRestError(f"Unexpected non-object feature in layer {layer_name!r}")
            properties = feature.get("properties")
            rows.append(dict(properties) if isinstance(properties, dict) else {})
            geometry = feature.get("geometry")
            geometries.append(shapely_shape(geometry) if geometry else None)
        frame = gpd.GeoDataFrame(rows, geometry=geometries, crs=crs)
    else:
        columns = _field_names_from_metadata(metadata)
        frame = gpd.GeoDataFrame(
            {column: [] for column in columns},
            geometry=gpd.GeoSeries([], crs=crs),
        )
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_file(target, layer=layer_name, driver="GPKG")
    return target


def download_arcgis_layers(
    service_url: str,
    output_path: str | Path,
    *,
    names: Sequence[str] | None = None,
    pattern: str | None = None,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int | None = None,
    bbox: BoundingBox | None = None,
    page_size: int | None = None,
    manifest_path: str | Path | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Discover, select, page, and write layers; return the provenance manifest.

    This is the single entry point the CLI and callers should use; the
    discovery/selection/pagination/writing steps above are also exported
    individually for direct testing and for callers that need finer control.
    """
    layers = discover_layers(service_url, opener=opener, timeout=timeout)
    selected = select_layers(layers, names=names, pattern=pattern)
    downloaded_at = utc_now_iso()
    records: list[SourceLayerRecord] = []
    for layer in selected:
        metadata = fetch_layer_metadata(
            service_url, layer.layer_id, opener=opener, timeout=timeout
        )
        features, query_info = download_layer_features(
            service_url, layer.layer_id, metadata=metadata, where=where,
            out_fields=out_fields, out_sr=out_sr, bbox=bbox, page_size=page_size,
            opener=opener, timeout=timeout,
        )
        output_layer_name = sanitize_layer_name(layer.name, layer.layer_id)
        write_features_to_gpkg(
            features, output_path, output_layer_name, metadata=metadata,
            crs_from_response=query_info.get("crs"), out_sr=out_sr,
        )
        records.append(SourceLayerRecord(
            output_layer=output_layer_name,
            source_url=query_info["base_query_url"],
            query_parameters=query_info["base_query_parameters"],
            feature_count=len(features),
            crs=_resolve_crs(query_info.get("crs"), out_sr),
            layer_metadata={
                "layer_id": layer.layer_id,
                "name": layer.name,
                "layer_type": layer.layer_type,
                "geometry_type": layer.geometry_type,
                "parent_layer_id": layer.parent_layer_id,
                "object_id_field": query_info.get("object_id_field"),
                "server_max_record_count": query_info.get("server_max_record_count"),
                "effective_page_size": query_info.get("effective_page_size"),
            },
        ))
    manifest = build_manifest(
        provider="arcgis_rest", source=service_url, output_path=output_path,
        records=records, downloaded_at=downloaded_at,
    )
    if manifest_path is not None:
        write_manifest(manifest_path, manifest)
    return manifest

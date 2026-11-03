"""Generic Socrata (SODA) geospatial dataset downloader.

Talks to any public Socrata open-data portal: retrieves dataset metadata
before downloading, pages through the dataset's own ``.geojson`` export with
``$limit``/``$offset``, optionally filters by a bounding box when the
dataset declares a spatial column, and writes the result to a GeoPackage
with the same provenance manifest format :mod:`arcgis_rest` uses.

Nothing here knows about any specific portal's dataset catalog or
stormwater semantics.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..watershed_data.network import download_bytes
from ._gpkg import require_geopandas, sanitize_layer_name
from .arcgis_rest import BoundingBox
from .manifest import SourceLayerRecord, build_manifest, utc_now_iso, write_manifest

# Socrata's own geojson export is always WGS84 (its spatial columns store
# lon/lat natively); there is no outSR-equivalent reprojection parameter.
SOCRATA_OUTPUT_CRS = "EPSG:4326"

# dataTypeName values Socrata uses for spatial columns (api/views metadata).
_GEO_DATA_TYPES = {
    "point", "location", "line", "multiline", "multipoint",
    "polygon", "multipolygon",
}


class SocrataError(ValueError):
    """Raised when a Socrata response violates its own contract."""


def dataset_metadata_url(domain: str, dataset_id: str) -> str:
    return f"https://{domain.strip('/')}/api/views/{dataset_id}.json"


def dataset_geojson_url(domain: str, dataset_id: str) -> str:
    return f"https://{domain.strip('/')}/resource/{dataset_id}.geojson"


def _get_json(
    url: str,
    params: dict[str, Any],
    *,
    opener: Callable[..., object],
    timeout: float,
    label: str,
    app_token: str | None = None,
) -> tuple[Any, str]:
    full_url = url + (("?" + urllib.parse.urlencode(params)) if params else "")
    request: Any = full_url
    if app_token:
        request = urllib.request.Request(full_url, headers={"X-App-Token": app_token})
    raw, _, _ = download_bytes(request, opener=opener, timeout=timeout, label=label)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SocrataError(f"{label}: response is not valid JSON") from exc
    if isinstance(payload, dict) and "message" in payload and ("error" in payload or "code" in payload):
        raise SocrataError(f"{label}: Socrata returned an error: {payload}")
    return payload, full_url


def fetch_dataset_metadata(
    domain: str,
    dataset_id: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
    app_token: str | None = None,
) -> dict[str, Any]:
    """Fetch the dataset's own metadata, including column names/types."""
    url = dataset_metadata_url(domain, dataset_id)
    payload, _ = _get_json(
        url, {}, opener=opener, timeout=timeout, app_token=app_token,
        label=f"Socrata dataset {dataset_id} metadata",
    )
    if not isinstance(payload, dict):
        raise SocrataError(f"Socrata dataset {dataset_id} metadata response is not a JSON object")
    return payload


def geo_column_name(metadata: dict[str, Any]) -> str | None:
    """The field name of the dataset's first spatial column, if any."""
    for column in metadata.get("columns") or []:
        if isinstance(column, dict) and column.get("dataTypeName") in _GEO_DATA_TYPES:
            field_name = column.get("fieldName")
            if isinstance(field_name, str) and field_name:
                return field_name
    return None


def _bbox_where_clause(geo_column: str, bbox: BoundingBox) -> str:
    # SoQL within_box(column, north-lat, west-lon, south-lat, east-lon).
    return f"within_box({geo_column}, {bbox.north}, {bbox.west}, {bbox.south}, {bbox.east})"


def _combine_where(user_where: str | None, bbox_clause: str | None) -> str | None:
    clauses = [clause for clause in (user_where, bbox_clause) if clause]
    if not clauses:
        return None
    return " AND ".join(f"({clause})" for clause in clauses)


def download_dataset_features(
    domain: str,
    dataset_id: str,
    *,
    metadata: dict[str, Any] | None = None,
    select: str = "*",
    where: str | None = None,
    bbox: BoundingBox | None = None,
    order_by: str | None = None,
    page_size: int = 1000,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
    max_pages: int = 10_000,
    app_token: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Page through a dataset's ``.geojson`` export with ``$limit``/``$offset``.

    Raises :class:`SocrataError` if a bounding box is requested but the
    dataset declares no spatial column, instead of silently returning an
    unfiltered result.
    """
    if page_size <= 0:
        raise SocrataError("Socrata page_size must be positive")
    if metadata is None:
        metadata = fetch_dataset_metadata(
            domain, dataset_id, opener=opener, timeout=timeout, app_token=app_token
        )
    bbox_clause = None
    if bbox is not None:
        column = geo_column_name(metadata)
        if column is None:
            raise SocrataError(
                f"Socrata dataset {dataset_id} declares no spatial column; "
                "cannot apply a bounding-box filter"
            )
        bbox_clause = _bbox_where_clause(column, bbox)
    combined_where = _combine_where(where, bbox_clause)
    stable_order = order_by or ":id"

    base_params: dict[str, Any] = {"$select": select, "$order": stable_order}
    if combined_where:
        base_params["$where"] = combined_where

    url = dataset_geojson_url(domain, dataset_id)
    features: list[dict[str, Any]] = []
    previous_page_signature: tuple[Any, ...] | None = None
    offset = 0
    for _ in range(max_pages):
        params = dict(base_params, **{"$limit": page_size, "$offset": offset})
        payload, _ = _get_json(
            url, params, opener=opener, timeout=timeout, app_token=app_token,
            label=f"Socrata dataset {dataset_id} query at offset {offset}",
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise SocrataError(
                f"Socrata dataset {dataset_id} query response has no features array"
            )
        page = payload["features"]
        page_count = len(page)
        if page_count == 0:
            break
        # Content-based duplicate-page protection: Socrata pages are
        # ordered by a stable column, so two consecutive pages with
        # identical content mean the server did not advance.
        signature = tuple(json.dumps(feature, sort_keys=True) for feature in page)
        if signature == previous_page_signature:
            raise SocrataError(
                f"Socrata dataset {dataset_id}: duplicate page at offset {offset} "
                "(server returned the same page twice); aborting rather than "
                "duplicating features"
            )
        previous_page_signature = signature
        features.extend(page)
        if page_count < page_size:
            break
        offset += page_count
    else:
        raise SocrataError(
            f"Socrata dataset {dataset_id}: exceeded max_pages={max_pages} while paginating"
        )

    query_info = {
        "base_query_url": url,
        "base_query_parameters": dict(base_params),
        "page_size": page_size,
        "geo_column": geo_column_name(metadata),
    }
    return features, query_info


def write_features_to_gpkg(
    features: Sequence[dict[str, Any]],
    output_path: str | Path,
    layer_name: str,
    *,
    metadata: dict[str, Any],
) -> Path:
    """Write GeoJSON-shaped features to one GeoPackage layer.

    Mirrors :func:`ohqbuilder.sources.arcgis_rest.write_features_to_gpkg`:
    attribute values -- including ``None`` -- pass through unchanged, and an
    empty result still writes a layer with the schema derived from the
    dataset's own column metadata.
    """
    gpd = require_geopandas()
    from shapely.geometry import shape as shapely_shape

    if features:
        rows: list[dict[str, Any]] = []
        geometries = []
        for feature in features:
            if not isinstance(feature, dict):
                raise SocrataError(f"Unexpected non-object feature in layer {layer_name!r}")
            properties = feature.get("properties")
            rows.append(dict(properties) if isinstance(properties, dict) else {})
            geometry = feature.get("geometry")
            geometries.append(shapely_shape(geometry) if geometry else None)
        frame = gpd.GeoDataFrame(rows, geometry=geometries, crs=SOCRATA_OUTPUT_CRS)
    else:
        columns = [
            column.get("fieldName")
            for column in metadata.get("columns") or []
            if isinstance(column, dict) and column.get("fieldName")
        ]
        frame = gpd.GeoDataFrame(
            {column: [] for column in columns},
            geometry=gpd.GeoSeries([], crs=SOCRATA_OUTPUT_CRS),
        )
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_file(target, layer=layer_name, driver="GPKG")
    return target


def download_socrata_dataset(
    domain: str,
    dataset_id: str,
    output_path: str | Path,
    *,
    output_layer: str | None = None,
    select: str = "*",
    where: str | None = None,
    bbox: BoundingBox | None = None,
    order_by: str | None = None,
    page_size: int = 1000,
    manifest_path: str | Path | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
    timeout: float = 60.0,
    app_token: str | None = None,
) -> dict[str, Any]:
    """Fetch metadata, page through the dataset, write a GeoPackage layer,
    and return the provenance manifest."""
    metadata = fetch_dataset_metadata(
        domain, dataset_id, opener=opener, timeout=timeout, app_token=app_token
    )
    downloaded_at = utc_now_iso()
    features, query_info = download_dataset_features(
        domain, dataset_id, metadata=metadata, select=select, where=where,
        bbox=bbox, order_by=order_by, page_size=page_size, opener=opener,
        timeout=timeout, app_token=app_token,
    )
    layer_name = output_layer or sanitize_layer_name(
        metadata.get("name") if isinstance(metadata.get("name"), str) else None,
        dataset_id,
    )
    write_features_to_gpkg(features, output_path, layer_name, metadata=metadata)
    record = SourceLayerRecord(
        output_layer=layer_name,
        source_url=query_info["base_query_url"],
        query_parameters=query_info["base_query_parameters"],
        feature_count=len(features),
        crs=SOCRATA_OUTPUT_CRS,
        layer_metadata={
            "dataset_id": dataset_id,
            "name": metadata.get("name"),
            "geo_column": query_info.get("geo_column"),
            "page_size": query_info.get("page_size"),
        },
    )
    manifest = build_manifest(
        provider="socrata", source=f"{domain}/{dataset_id}", output_path=output_path,
        records=[record], downloaded_at=downloaded_at,
    )
    if manifest_path is not None:
        write_manifest(manifest_path, manifest)
    return manifest

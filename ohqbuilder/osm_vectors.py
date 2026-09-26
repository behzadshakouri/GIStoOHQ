"""Fetch explicitly bounded OSM waterways or roads as source vectors.

OSM ways are reference geometry, not a routed catchment or an NHDPlus network.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import urllib.parse
import urllib.request


OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def overpass_query(bounds: tuple[float, float, float, float], product: str) -> str:
    """Build a bounded Overpass query from (west, south, east, north)."""

    west, south, east, north = bounds
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("OSM bounds must contain finite coordinates")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("OSM bounds must be ordered WGS84 west,south,east,north")
    if (east - west) * (north - south) > 1:
        raise ValueError("OSM bounding box exceeds one square degree; use smaller queries")
    if product not in {"hydro", "roads"}:
        raise ValueError("OSM product must be hydro or roads")
    key = "waterway" if product == "hydro" else "highway"
    pattern = "^(river|stream)$" if product == "hydro" else "."
    return (
        f'[out:json][timeout:120];way["{key}"~"{pattern}"]'
        f"({south},{west},{north},{east});out geom;"
    )


def overpass_geojson(payload: dict, product: str) -> dict:
    """Keep only complete line ways, preserving IDs and source tags."""

    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("Overpass response has no elements array")
    features = []
    seen = set()
    for element in payload["elements"]:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        tags = element.get("tags") or {}
        if not isinstance(tags, dict):
            continue
        if product == "hydro" and tags.get("waterway") not in {"river", "stream"}:
            continue
        if product == "roads" and not tags.get("highway"):
            continue
        identifier = element.get("id")
        if not isinstance(identifier, int) or identifier in seen:
            continue
        geometry = element.get("geometry") or []
        if not isinstance(geometry, list):
            continue
        coordinates = []
        for point in geometry:
            if not isinstance(point, dict):
                break
            lon, lat = point.get("lon"), point.get("lat")
            if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
                break
            if not math.isfinite(lon) or not math.isfinite(lat):
                break
            coordinates.append([lon, lat])
        else:
            if len(coordinates) >= 2:
                seen.add(identifier)
                features.append({
                    "type": "Feature",
                    "id": identifier,
                    "properties": {
                        "osm_id": identifier,
                        "name": str(tags.get("name", "")),
                        "waterway": str(tags.get("waterway", "")),
                        "highway": str(tags.get("highway", "")),
                        "source": "OpenStreetMap contributors / Overpass API",
                    },
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                })
    if not features:
        raise ValueError(f"No complete OSM {product} ways intersect the query bounds")
    return {"type": "FeatureCollection", "features": features}


def download_osm_vectors(
    bounds: tuple[float, float, float, float], product: str, output: str | Path
) -> Path:
    """Download and atomically write WGS84 GeoJSON for later materialization."""

    query = overpass_query(bounds, product)
    request = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": "GIStoOHQ/0.1 (OSM vector acquisition)"},
    )
    with urllib.request.urlopen(request, timeout=150) as response:  # noqa: S310
        payload = json.load(response)
    geojson = overpass_geojson(payload, product)
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(json.dumps(geojson, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target

"""Internal helpers shared by the ArcGIS REST and Socrata downloaders.

Not part of the public API of :mod:`ohqbuilder.sources` -- both downloader
modules import from here so the lazy geopandas import and output-layer-name
sanitization live in exactly one place instead of two copies.
"""

from __future__ import annotations

import re


def require_geopandas():
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError(
            "Writing GeoPackage output requires geopandas/fiona/shapely. "
            "Install optional dependencies with: pip install -e .[gis]"
        ) from exc
    return gpd


def sanitize_layer_name(base: str | None, suffix: str | int) -> str:
    """A GeoPackage-safe layer name derived from a provider-supplied name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", base or "").strip("_")
    if not cleaned:
        cleaned = "layer"
    if not cleaned[0].isalpha() and cleaned[0] != "_":
        cleaned = f"l_{cleaned}"
    return f"{cleaned}_{suffix}"

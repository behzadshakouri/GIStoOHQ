from __future__ import annotations
from pathlib import Path
from typing import Any

def _try_import_geopandas():
    try:
        import geopandas as gpd
        return gpd
    except ImportError as exc:
        raise ImportError("Reading GeoPackage files requires geopandas/fiona. Install optional dependencies with: pip install -e .[gis]") from exc

def read_layer(path: Path, layer: str | None = None):
    gpd = _try_import_geopandas()
    if layer:
        return gpd.read_file(path, layer=layer)
    return gpd.read_file(path)

def row_get(row: Any, key: str, default=None):
    try:
        val = row[key]
    except Exception:
        return default
    try:
        if val != val:
            return default
    except Exception:
        pass
    return val

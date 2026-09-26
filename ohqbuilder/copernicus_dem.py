"""Acquire bounded Copernicus GLO-30 Public source tiles from its public index."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import urllib.request


BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
MAX_TILE_BYTES = 250 * 1024 * 1024


def tile_names(bounds: tuple[float, float, float, float], *, max_tiles: int = 16) -> list[str]:
    """List 1-degree tiles intersecting WGS84 (west,south,east,north)."""

    west, south, east, north = bounds
    if not all(math.isfinite(v) for v in bounds):
        raise ValueError("DEM bounds must be finite")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("DEM bounds must be ordered WGS84 west,south,east,north")
    if max_tiles < 1:
        raise ValueError("max_tiles must be positive")
    names = [
        f"Copernicus_DSM_COG_10_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_"
        f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM"
        for lat in range(math.floor(south), math.ceil(north))
        for lon in range(math.floor(west), math.ceil(east))
    ]
    if len(names) > max_tiles:
        raise ValueError(f"DEM bounds require {len(names)} tiles; limit is {max_tiles}")
    return names


def _validate_raster(path: Path) -> None:
    try:
        import rasterio
    except ImportError as exc:
        raise RuntimeError("DEM verification requires rasterio (`pip install -e .[gis]`)") from exc
    with rasterio.open(path) as raster:
        if raster.count < 1 or raster.width < 1 or raster.height < 1 or raster.crs is None:
            raise ValueError(f"Invalid Copernicus DEM raster: {path}")
        if raster.crs.to_epsg() != 4326:
            raise ValueError(f"Copernicus DEM tile is not EPSG:4326: {path}")
        match = re.fullmatch(
            r"Copernicus_DSM_COG_10_([NS])(\d{2})_00_([EW])(\d{3})_00_DEM\.tif",
            path.name,
        )
        if match is None:
            raise ValueError(f"Unexpected Copernicus DEM tile filename: {path.name}")
        south = int(match[2]) * (1 if match[1] == "N" else -1)
        west = int(match[4]) * (1 if match[3] == "E" else -1)
        bounds = raster.bounds
        if not (abs(bounds.left - west) < 0.01 and abs(bounds.top - (south + 1)) < 0.01):
            raise ValueError(f"Copernicus DEM tile extent disagrees with its filename: {path}")
        # Read every block so a truncated file fails before GIS processing.
        for _, window in raster.block_windows(1):
            raster.read(1, window=window)


def _download_tile(url: str, path: Path) -> str:
    temporary = path.with_name(path.name + ".partial")
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as sink:  # noqa: S310
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_TILE_BYTES:
                    raise ValueError(f"Copernicus tile exceeds {MAX_TILE_BYTES} bytes: {url}")
                sink.write(block)
                digest.update(block)
        if total == 0:
            raise ValueError(f"Empty Copernicus tile: {url}")
        _validate_raster(temporary)
        temporary.replace(path)
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def download_copernicus_dem(
    bounds: tuple[float, float, float, float], output_dir: str | Path, *, max_tiles: int = 16
) -> Path:
    """Fetch indexed GLO-30 tiles, verify rasters, and write a source manifest.

    A missing public tile fails explicitly; a lower-resolution source may be
    selected later by the operator rather than silently substituted.
    """

    names = tile_names(bounds, max_tiles=max_tiles)
    with urllib.request.urlopen(f"{BASE_URL}/tileList.txt", timeout=60) as response:  # noqa: S310
        available = set(response.read().decode("utf-8").splitlines())
    missing = [name for name in names if name not in available]
    if missing:
        raise ValueError("GLO-30 Public does not list tile(s): " + ", ".join(missing))
    folder = Path(output_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    tiles = []
    for name in names:
        url = f"{BASE_URL}/{name}/{name}.tif"
        path = folder / f"{name}.tif"
        if path.exists():
            _validate_raster(path)
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            checksum = digest.hexdigest()
        else:
            checksum = _download_tile(url, path)
        tiles.append({"path": path.name, "url": url, "sha256": checksum})
    manifest = folder / "copernicus_dem_source.json"
    content = {"dataset": "Copernicus DEM GLO-30 Public", "bounds_wgs84": bounds,
               "tiles": tiles, "index_url": f"{BASE_URL}/tileList.txt"}
    temporary = manifest.with_name(manifest.name + ".tmp")
    try:
        temporary.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        temporary.replace(manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest

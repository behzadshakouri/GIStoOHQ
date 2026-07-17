from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


class HydroMaterializeError(RuntimeError):
    """Raised when downloaded hydrography cannot be converted to flowlines."""


@dataclass(frozen=True)
class HydroMaterializeResult:
    output_path: Path
    source_count: int
    feature_count: int


def materialize_flowlines(
    root: str | Path,
    site: str,
    *,
    source_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    dem_path: str | Path | None = None,
) -> HydroMaterializeResult:
    """Extract NHD flowlines, clip them to the DEM extent, and write a GeoPackage."""
    try:
        import geopandas as gpd
        import pandas as pd
        import rasterio
        from shapely.geometry import box
    except ImportError as exc:  # pragma: no cover - optional GIS environment
        raise HydroMaterializeError(
            "Hydrography materialization requires `pip install -e .[gis]`."
        ) from exc

    site_path = Path(root).expanduser().resolve() / site
    sources = Path(source_dir).expanduser().resolve() if source_dir else site_path / "source_downloads"
    target = Path(output_path).expanduser().resolve() if output_path else site_path / "outputs" / "NHDFlowline_clip.gpkg"
    dem = Path(dem_path).expanduser().resolve() if dem_path else site_path / "demlr" / "cliped_utm.tif"
    if not sources.exists():
        raise HydroMaterializeError(f"Hydrography download directory not found: {sources}")
    if not dem.is_file():
        raise HydroMaterializeError(f"Materialized DEM not found: {dem}")

    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        for archive_path in sources.rglob("*.zip"):
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(workspace / archive_path.stem)
            except zipfile.BadZipFile as exc:
                raise HydroMaterializeError(f"Invalid hydrography archive: {archive_path}") from exc

        candidates = list(sources.rglob("*.shp")) + list(workspace.rglob("*.shp"))
        candidates = [path for path in candidates if "flowline" in path.stem.lower()]
        if not candidates:
            raise HydroMaterializeError(
                f"No NHD flowline shapefile was found under downloaded products: {sources}"
            )
        frames = []
        for candidate in candidates:
            frame = gpd.read_file(candidate)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise HydroMaterializeError("Downloaded NHD flowline layers contain no features.")
        base_crs = frames[0].crs
        if base_crs is None:
            raise HydroMaterializeError("Downloaded NHD flowlines have no CRS.")
        combined = gpd.GeoDataFrame(
            pd.concat([frame.to_crs(base_crs) for frame in frames], ignore_index=True),
            crs=base_crs,
        )
        with rasterio.open(dem) as dataset:
            if dataset.crs is None:
                raise HydroMaterializeError(f"Materialized DEM has no CRS: {dem}")
            combined = combined.to_crs(dataset.crs)
            clipped = combined[combined.intersects(box(*dataset.bounds))].copy()
            clipped = clipped.clip(box(*dataset.bounds))
        if clipped.empty:
            raise HydroMaterializeError("No downloaded flowlines intersect the materialized DEM.")
        target.parent.mkdir(parents=True, exist_ok=True)
        clipped.to_file(target, layer="NHDFlowline_clip", driver="GPKG")
    return HydroMaterializeResult(target, len(candidates), len(clipped))

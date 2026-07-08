from __future__ import annotations

import importlib.util
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

RASTER_SUFFIXES = {".tif", ".tiff", ".img"}


class DemMaterializeError(RuntimeError):
    """Raised when raw DEM source products cannot be materialized."""


@dataclass(frozen=True)
class DemMaterializeResult:
    output_path: Path
    source_count: int
    dst_crs: str


def _require_rasterio() -> None:
    if importlib.util.find_spec("rasterio") is None:
        raise DemMaterializeError(
            "Materializing demlr/cliped_utm.tif requires rasterio. "
            "Install GIS dependencies with `pip install -e .[gis]`."
        )


def discover_dem_sources(source_dir: str | Path) -> list[Path]:
    """Return local raster files and zip archives that may contain DEM rasters."""

    root = Path(source_dir).expanduser().resolve()
    if not root.exists():
        return []
    candidates: list[Path] = []
    for path in root.rglob("*"):
        suffix = path.suffix.lower()
        if path.is_file() and (suffix in RASTER_SUFFIXES or suffix == ".zip"):
            candidates.append(path)
    return sorted(candidates)


def _extract_zip_rasters(zip_path: Path, destination: Path) -> list[Path]:
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            suffix = Path(member.filename).suffix.lower()
            if member.is_dir() or suffix not in RASTER_SUFFIXES:
                continue
            target = destination / zip_path.stem / member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append(target)
    return extracted


def _expand_sources(sources: list[Path], workspace: Path) -> list[Path]:
    rasters: list[Path] = []
    for source in sources:
        if source.suffix.lower() == ".zip":
            rasters.extend(_extract_zip_rasters(source, workspace))
        elif source.suffix.lower() in RASTER_SUFFIXES:
            rasters.append(source)
    return rasters


def utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _infer_dst_crs(first_raster: Path) -> str:
    from rasterio.warp import transform

    import rasterio

    with rasterio.open(first_raster) as src:
        left, bottom, right, top = src.bounds
        x = (left + right) / 2
        y = (bottom + top) / 2
        if src.crs and src.crs.to_epsg() != 4326:
            lon_values, lat_values = transform(src.crs, "EPSG:4326", [x], [y])
            lon = lon_values[0]
            lat = lat_values[0]
        else:
            lon = x
            lat = y
    return f"EPSG:{utm_epsg_from_lonlat(lon, lat)}"


def materialize_dem(
    root: str | Path,
    site: str,
    *,
    source_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    dst_crs: str | None = None,
) -> DemMaterializeResult:
    """Mosaic/reproject downloaded DEM rasters into the legacy DEM filename."""

    _require_rasterio()
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    root_path = Path(root).expanduser().resolve()
    site_path = root_path / site
    source_path = Path(source_dir).expanduser().resolve() if source_dir else site_path / "source_downloads"
    target = Path(output_path).expanduser().resolve() if output_path else site_path / "demlr" / "cliped_utm.tif"
    sources = discover_dem_sources(source_path)
    if not sources:
        raise DemMaterializeError(f"No DEM rasters or zip archives found under: {source_path}")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rasters = _expand_sources(sources, Path(tmp))
        if not rasters:
            raise DemMaterializeError(f"No DEM rasters found after expanding sources under: {source_path}")
        selected_crs = dst_crs or _infer_dst_crs(rasters[0])
        datasets = [rasterio.open(path) for path in rasters]
        try:
            mosaic, transform = merge(datasets)
            profile = datasets[0].profile.copy()
        finally:
            for dataset in datasets:
                dataset.close()

        profile.update(
            driver="GTiff",
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=transform,
            count=mosaic.shape[0],
        )
        with tempfile.NamedTemporaryFile(suffix=".tif", dir=target.parent, delete=False) as tmp_raster:
            mosaic_path = Path(tmp_raster.name)
        with rasterio.open(mosaic_path, "w", **profile) as dst:
            dst.write(mosaic)

        with rasterio.open(mosaic_path) as src:
            dst_transform, width, height = calculate_default_transform(
                src.crs,
                selected_crs,
                src.width,
                src.height,
                *src.bounds,
            )
            dst_profile = src.profile.copy()
            dst_profile.update(crs=selected_crs, transform=dst_transform, width=width, height=height)
            with rasterio.open(target, "w", **dst_profile) as dst:
                for band_index in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, band_index),
                        destination=rasterio.band(dst, band_index),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=dst_transform,
                        dst_crs=selected_crs,
                        resampling=Resampling.bilinear,
                    )
        mosaic_path.unlink(missing_ok=True)
    return DemMaterializeResult(target, len(sources), selected_crs)

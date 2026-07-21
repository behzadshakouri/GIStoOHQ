from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dem_materializer import DemMaterializeResult, materialize_dem, bounds_from_lonlat_buffer, parse_bounds
from .hydro_materializer import HydroMaterializeResult, materialize_flowlines


@dataclass(frozen=True)
class SourceMaterializeResult:
    dem: DemMaterializeResult
    hydro: HydroMaterializeResult


def find_product_dir(source_dir: str | Path, product: str) -> Path:
    """Find one exact product directory in a per-site download tree."""

    source = Path(source_dir).expanduser().resolve()
    matches = sorted(path for path in source.rglob(product) if path.is_dir())
    if not matches:
        raise FileNotFoundError(f"Downloaded {product} directory not found under {source}")
    if len(matches) > 1:
        names = ", ".join(str(path) for path in matches)
        raise ValueError(f"Multiple downloaded {product} directories found: {names}")
    return matches[0]


def materialize_source_inputs(
    root: str | Path,
    site: str,
    *,
    source_dir: str | Path | None = None,
    target_crs: str | None = None,
    clip_bounds: str | tuple[float, float, float, float] | None = None,
    clip_bounds_crs: str = "EPSG:4326",
    clip_center_lon: float | None = None,
    clip_center_lat: float | None = None,
    clip_buffer_m: float | None = None,
    clip_buffer_scale: float = 1.1,
) -> SourceMaterializeResult:
    """Merge/project the DEM and extract/clip hydrography in one stage."""

    root_path = Path(root).expanduser().resolve()
    downloads = (
        Path(source_dir).expanduser().resolve()
        if source_dir
        else root_path / site / "source_downloads"
    )
    selected_bounds = parse_bounds(clip_bounds)
    if selected_bounds is None and (
        clip_center_lon is not None and clip_center_lat is not None and clip_buffer_m is not None
    ):
        selected_bounds = bounds_from_lonlat_buffer(
            clip_center_lon,
            clip_center_lat,
            clip_buffer_m,
            scale=clip_buffer_scale,
        )
    dem = materialize_dem(
        root_path,
        site,
        source_dir=find_product_dir(downloads, "demlr"),
        dst_crs=target_crs,
        clip_bounds=selected_bounds,
        clip_bounds_crs=clip_bounds_crs,
    )
    hydro = materialize_flowlines(
        root_path,
        site,
        source_dir=find_product_dir(downloads, "hydro"),
        dem_path=dem.output_path,
    )
    return SourceMaterializeResult(dem, hydro)

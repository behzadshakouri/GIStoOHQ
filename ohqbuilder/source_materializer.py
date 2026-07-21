from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

from .dem_materializer import DemMaterializeResult, materialize_dem, bounds_from_lonlat_buffer, parse_bounds
from .hydro_materializer import HydroMaterializeResult, materialize_flowlines


@dataclass(frozen=True)
class SourceMaterializeResult:
    dem: DemMaterializeResult
    hydro: HydroMaterializeResult
    landcover: Path | None = None
    cn_lookup: Path | None = None


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



def bundled_cn_lookup_path() -> Path:
    """Return the repository-bundled curve-number lookup table path."""

    return Path(__file__).resolve().parent.parent / "cn_lookup.csv"


def materialize_cn_lookup(root: Path, source: Path | None = None) -> Path:
    """Copy the bundled curve-number lookup table to the legacy ROOT path."""

    source_path = source or bundled_cn_lookup_path()
    if not source_path.is_file():
        raise FileNotFoundError(f"Bundled curve-number lookup table not found: {source_path}")
    root.mkdir(parents=True, exist_ok=True)
    target = root / "cn_lookup.csv"
    shutil.copyfile(source_path, target)
    return target


def materialize_landcover(root: Path, site: str, source_dir: Path) -> Path | None:
    """Copy a downloaded NLCD raster into the legacy Phase 2 expected path."""

    try:
        landcover_dir = find_product_dir(source_dir, "landcover")
    except FileNotFoundError:
        return None
    sources = sorted(landcover_dir.glob("nlcd_*.tif"))
    if not sources:
        return None
    source = sources[0]
    match = re.match(r"nlcd_(\d{4})_", source.name)
    year = match.group(1) if match else "2023"
    target_dir = root / site / "landcover"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"nlcd_{year}_{site}.tif"
    shutil.copyfile(source, target)
    aux = source.with_name(source.name + ".aux.xml")
    if aux.exists():
        shutil.copyfile(aux, target.with_name(target.name + ".aux.xml"))
    return target

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
    clip_buffer_scale: float = 1.2,
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
    landcover = materialize_landcover(root_path, site, downloads)
    cn_lookup = materialize_cn_lookup(root_path)
    return SourceMaterializeResult(dem, hydro, landcover, cn_lookup)

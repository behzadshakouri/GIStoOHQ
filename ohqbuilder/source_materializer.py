from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil

from .dem_materializer import DemMaterializeResult, bounds_from_lonlat_buffer, materialize_dem, parse_bounds
from .hydro_materializer import HydroMaterializeResult, materialize_flowlines
from .wbd_materializer import (
    WbdMaterializeError,
    materialize_wbd_reference,
    materialize_wbd_service_reference,
)


@dataclass(frozen=True)
class SourceMaterializeResult:
    dem: DemMaterializeResult
    hydro: HydroMaterializeResult
    landcover: Path | None = None
    cn_lookup: Path | None = None
    wbd_reference: Path | None = None


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


def demo_hydro_dir(root: Path) -> Path | None:
    """Return bundled hydro fixtures when downloaded hydro is unavailable."""

    candidate = root / "hydro"
    return candidate if candidate.is_dir() else None


def resolve_hydro_source_dir(root: Path, source_dir: Path) -> Path:
    """Find downloaded hydro products or fall back to bundled example hydro files."""

    try:
        return find_product_dir(source_dir, "hydro")
    except FileNotFoundError:
        fallback = demo_hydro_dir(root)
        if fallback is not None:
            return fallback
        raise


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


def materialize_optional_wbd(
    root: Path,
    site: str,
    source_dir: Path,
    bounds: tuple[float, float, float, float] | None,
    bounds_crs: str,
    *,
    allow_service_fallback: bool = True,
) -> Path | None:
    """Create a clipped HUC12 reference when both WBD data and bounds are available."""

    if bounds is None:
        return None
    target = root / site / "outputs" / "WBDHU12_reference.gpkg"
    hydro_fallback = False
    try:
        wbd_dir = find_product_dir(source_dir, "wbd")
    except FileNotFoundError:
        try:
            # NHDPlus HR vector distributions commonly carry WBDHU layers. Use
            # that authoritative copy instead of assuming TNM exposes WBD as a
            # separate point-query download product.
            wbd_dir = find_product_dir(source_dir, "hydro")
            hydro_fallback = True
        except FileNotFoundError:
            wbd_dir = None
    local_error = None
    if wbd_dir is not None:
        try:
            return materialize_wbd_reference(
                wbd_dir,
                target,
                clip_bounds=bounds,
                clip_bounds_crs=bounds_crs,
            )
        except WbdMaterializeError as exc:
            local_error = exc
    if not allow_service_fallback:
        if target.is_file():
            return target
        warning = root / site / "outputs" / "WBD_MATERIALIZATION_WARNING.txt"
        warning.parent.mkdir(parents=True, exist_ok=True)
        details = str(local_error) if local_error else "no local WBD vector package was found"
        warning.write_text(
            f"WBD reference unavailable from local downloads: {details}\n"
            "Offline/reuse mode disabled the official WBD web-service fallback.\n"
            "The DEM watershed workflow may continue without WBD validation.\n",
            encoding="utf-8",
        )
        return None
    try:
        return materialize_wbd_service_reference(
            target,
            clip_bounds=bounds,
            clip_bounds_crs=bounds_crs,
        )
    except WbdMaterializeError as exc:
        warning = root / site / "outputs" / "WBD_MATERIALIZATION_WARNING.txt"
        warning.parent.mkdir(parents=True, exist_ok=True)
        source_kind = (
            "hydro fallback"
            if hydro_fallback
            else "standalone WBD download or official WBD web service"
        )
        details = f"{local_error}; web service fallback: {exc}" if local_error else str(exc)
        warning.write_text(
            f"WBD reference unavailable from {source_kind}: {details}\n"
            "The DEM watershed workflow may continue, but no WBD validation was performed.\n",
            encoding="utf-8",
        )
        return None


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
    dem_manifest: str | Path | None = None,
    allow_network_fallbacks: bool = True,
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
    dem_source_dir = None if dem_manifest else find_product_dir(downloads, "demlr")
    dem = materialize_dem(
        root_path,
        site,
        source_dir=dem_source_dir,
        dst_crs=target_crs,
        clip_bounds=selected_bounds,
        clip_bounds_crs=clip_bounds_crs,
        manifest_path=dem_manifest,
    )
    hydro = materialize_flowlines(
        root_path,
        site,
        source_dir=resolve_hydro_source_dir(root_path, downloads),
        dem_path=dem.output_path,
    )
    landcover = materialize_landcover(root_path, site, downloads)
    cn_lookup = materialize_cn_lookup(root_path)
    wbd_reference = materialize_optional_wbd(
        root_path,
        site,
        downloads,
        selected_bounds,
        clip_bounds_crs,
        allow_service_fallback=allow_network_fallbacks,
    )
    return SourceMaterializeResult(dem, hydro, landcover, cn_lookup, wbd_reference)

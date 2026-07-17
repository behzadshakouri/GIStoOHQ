from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .phase1_fetcher import Phase1FetchResult, fetch_phase1_inputs
from .soil_retrieval import (
    SoilRetrievalResult,
    retrieve_hydrologic_soil_groups,
    retrieve_soil_texture,
)


@dataclass(frozen=True)
class InputDownloadResult:
    """Source products downloaded before merge, clip, and GIS preparation."""

    phase1: Phase1FetchResult
    hsg: SoilRetrievalResult
    texture: SoilRetrievalResult

    @property
    def download_dir(self) -> Path:
        return self.phase1.download_dir

def download_all_inputs(
    root: str | Path,
    site: str,
    *,
    lon: float,
    lat: float,
    site_id: str | None = None,
    download_dir: str | Path | None = None,
    buffer_m: float = 5000.0,
    max_tiles: int | None = None,
    soil_pixel_size: float = 0.0003,
    soil_top_depth: float = 30.0,
) -> InputDownloadResult:
    """Download every Python-supported source input for one site.

    Soil retrieval normally uses a delineated watershed. At this first pipeline
    step no boundary exists yet, so both USDA queries use the supplied outlet
    coordinate and buffer. A later workflow may re-query against the delineated
    boundary when exact watershed coverage is required.
    """

    if not -180.0 <= lon <= 180.0:
        raise ValueError("longitude must be between -180 and 180 degrees")
    if not -90.0 <= lat <= 90.0:
        raise ValueError("latitude must be between -90 and 90 degrees")
    if buffer_m <= 0:
        raise ValueError("buffer_m must be greater than zero")
    if soil_pixel_size <= 0:
        raise ValueError("soil_pixel_size must be greater than zero")
    if soil_top_depth <= 0:
        raise ValueError("soil_top_depth must be greater than zero")

    phase1 = fetch_phase1_inputs(
        root,
        site,
        lon=lon,
        lat=lat,
        site_id=site_id,
        products="all",
        download_dir=download_dir,
        buffer_m=buffer_m,
        max_tiles=max_tiles,
    )
    center = (lon, lat)
    hsg = retrieve_hydrologic_soil_groups(
        root,
        site,
        buffer=buffer_m,
        pixel_size=soil_pixel_size,
        center=center,
    )
    texture = retrieve_soil_texture(
        root,
        site,
        buffer=buffer_m,
        pixel_size=soil_pixel_size,
        top_depth=soil_top_depth,
        center=center,
    )
    return InputDownloadResult(phase1, hsg, texture)

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .usda import bbox_wkt, query_sda, site_soils_dir

SoilKind = Literal["hsg", "texture"]


class SoilRetrievalError(RuntimeError):
    """Raised when USDA SDA soil retrieval cannot complete."""


@dataclass(frozen=True)
class SoilRetrievalResult:
    output_dir: Path
    vector_path: Path
    raster_paths: tuple[Path, ...]
    row_count: int


def _require_gis() -> None:
    missing = [name for name in ("geopandas", "shapely") if importlib.util.find_spec(name) is None]
    if missing:
        raise SoilRetrievalError(
            "Soil retrieval requires GIS Python dependencies. Install with `pip install -e .[gis]`."
        )


def _watershed_bounds(root: Path, site: str, buffer: float) -> tuple[float, float, float, float]:
    _require_gis()
    import geopandas as gpd

    boundary = root / site / "outputs" / "watershed_boundary.gpkg"
    if not boundary.is_file():
        raise SoilRetrievalError(f"Watershed boundary not found: {boundary}")
    gdf = gpd.read_file(boundary).to_crs("EPSG:4326")
    minx, miny, maxx, maxy = gdf.total_bounds
    # Buffer is intentionally converted approximately for the WGS84 SDA request.
    degree_buffer = buffer / 111_320.0
    return (minx - degree_buffer, miny - degree_buffer, maxx + degree_buffer, maxy + degree_buffer)


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    _require_gis()
    import geopandas as gpd
    from shapely.geometry import Point

    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        gdf = gpd.GeoDataFrame({"message": ["No SDA rows returned"]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    else:
        gdf = gpd.GeoDataFrame(rows, geometry=[Point(0, 0) for _ in rows], crs="EPSG:4326")
    gdf.to_file(path, driver="GPKG")


def _write_placeholder_raster(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Placeholder created by USDA SDA retrieval. Rasterization requires site-specific GIS processing.\n",
        encoding="utf-8",
    )


def _query_hsg(wkt: str) -> list[dict[str, object]]:
    sql = f"""
    SELECT mukey, muname, hydgrpdcd AS hydrologic_group
    FROM mapunit
    WHERE mukey IN (SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}'))
    """
    return query_sda(sql)


def _query_texture(wkt: str, top_depth: float) -> list[dict[str, object]]:
    sql = f"""
    SELECT mu.mukey, mu.muname, ch.texcl AS texture_class,
           ch.sandtotal_r AS sand_pct, ch.silttotal_r AS silt_pct, ch.claytotal_r AS clay_pct
    FROM mapunit AS mu
    INNER JOIN component AS co ON co.mukey = mu.mukey
    INNER JOIN chorizon AS ch ON ch.cokey = co.cokey
    WHERE mu.mukey IN (SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}'))
      AND ch.hzdept_r <= {top_depth}
    """
    return query_sda(sql)


def retrieve_hydrologic_soil_groups(
    root: str | Path,
    site: str,
    *,
    buffer: float = 5000.0,
    pixel_size: float = 0.0003,
) -> SoilRetrievalResult:
    root_path = Path(root).expanduser().resolve()
    soils_dir = site_soils_dir(root_path, site)
    wkt = bbox_wkt(_watershed_bounds(root_path, site, buffer))
    rows = _query_hsg(wkt)
    vector = soils_dir / "hydrologic_soil_groups.gpkg"
    raster = soils_dir / "hsg.tif"
    _write_rows(vector, rows)
    _write_placeholder_raster(raster)
    return SoilRetrievalResult(soils_dir, vector, (raster,), len(rows))


def retrieve_soil_texture(
    root: str | Path,
    site: str,
    *,
    buffer: float = 5000.0,
    pixel_size: float = 0.0003,
    top_depth: float = 30.0,
) -> SoilRetrievalResult:
    root_path = Path(root).expanduser().resolve()
    soils_dir = site_soils_dir(root_path, site)
    wkt = bbox_wkt(_watershed_bounds(root_path, site, buffer))
    rows = _query_texture(wkt, top_depth)
    vector = soils_dir / "soil_texture.gpkg"
    rasters = tuple(soils_dir / name for name in ("texture_code.tif", "sand_pct.tif", "silt_pct.tif", "clay_pct.tif"))
    _write_rows(vector, rows)
    for raster in rasters:
        _write_placeholder_raster(raster)
    return SoilRetrievalResult(soils_dir, vector, rasters, len(rows))

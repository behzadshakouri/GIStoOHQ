from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .usda import bbox_wkt, query_sda, site_soils_dir

SoilKind = Literal["hsg", "texture"]

HSG_CODE = {"A": 1, "B": 2, "C": 3, "D": 4}
TEXTURE_CODE = {
    "sand": 1,
    "loamy sand": 2,
    "sandy loam": 3,
    "loam": 4,
    "silt loam": 5,
    "silt": 6,
    "sandy clay loam": 7,
    "clay loam": 8,
    "silty clay loam": 9,
    "sandy clay": 10,
    "silty clay": 11,
    "clay": 12,
}
NODATA_PCT = -9999.0
MUKEY_CHUNK = 500


class SoilRetrievalError(RuntimeError):
    """Raised when USDA SDA soil retrieval cannot complete."""


@dataclass(frozen=True)
class SoilRetrievalResult:
    output_dir: Path
    vector_path: Path
    raster_paths: tuple[Path, ...]
    row_count: int


def _require_gis() -> None:
    missing = [
        name
        for name in ("geopandas", "shapely", "rasterio")
        if importlib.util.find_spec(name) is None
    ]
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
    degree_buffer = buffer / 111_320.0
    return (minx - degree_buffer, miny - degree_buffer, maxx + degree_buffer, maxy + degree_buffer)


def _query_bounds(
    root: Path,
    site: str,
    buffer: float,
    center: tuple[float, float] | None,
) -> tuple[float, float, float, float]:
    if center is None:
        return _watershed_bounds(root, site, buffer)
    lon, lat = center
    degree_buffer = buffer / 111_320.0
    return (
        lon - degree_buffer,
        lat - degree_buffer,
        lon + degree_buffer,
        lat + degree_buffer,
    )


def _write_vector(path: Path, layer: str, rows: list[dict[str, Any]], geom_col: str = "geom_wkt") -> None:
    _require_gis()
    import geopandas as gpd
    from shapely import wkt as shapely_wkt

    path.parent.mkdir(parents=True, exist_ok=True)
    features: list[dict[str, Any]] = []
    geometries = []
    for row in rows:
        geom_text = row.get(geom_col)
        if not geom_text:
            continue
        try:
            geom = shapely_wkt.loads(str(geom_text))
        except Exception as exc:  # pragma: no cover - defensive for malformed SDA geometry
            raise SoilRetrievalError(f"Invalid SDA geometry WKT in {path.name}: {exc}") from exc
        attrs = {key: value for key, value in row.items() if key != geom_col}
        features.append(attrs)
        geometries.append(geom)
    gdf = gpd.GeoDataFrame(features, geometry=geometries, crs="EPSG:4326")
    gdf.to_file(path, layer=layer, driver="GPKG")


def _rasterize(path: Path, vector: Path, attribute: str, *, pixel_size: float, dtype: str, nodata: float) -> None:
    _require_gis()
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds

    gdf = gpd.read_file(vector)
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.empty:
        path.write_text("No SDA polygons returned; raster not created.\n", encoding="utf-8")
        return
    minx, miny, maxx, maxy = gdf.total_bounds
    width = max(1, int(round((maxx - minx) / pixel_size)))
    height = max(1, int(round((maxy - miny) / pixel_size)))
    transform = from_bounds(minx, miny, maxx, maxy, width, height)
    shapes = ((geom, value) for geom, value in zip(gdf.geometry, gdf[attribute]) if geom is not None)
    arr = rasterize(shapes, out_shape=(height, width), transform=transform, fill=nodata, dtype=dtype)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=arr.dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=nodata,
        compress="lzw",
    ) as dst:
        dst.write(arr, 1)


def _resolve_hsg(raw: object) -> str:
    if raw is None:
        return ""
    value = str(raw).strip().upper()
    if not value:
        return ""
    return value.split("/")[-1].strip() if "/" in value else value


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cokey_sort_key(cokey: str) -> tuple[int, int | str]:
    try:
        return (0, int(cokey))
    except ValueError:
        return (1, cokey)


def _validate_positive(name: str, value: float) -> None:
    if value <= 0:
        raise SoilRetrievalError(f"{name} must be greater than 0; got {value}")


def _usda_texture(sand: float, silt: float, clay: float) -> str:
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52 and silt + 2 * clay >= 30) or (
        clay < 7 and silt < 50 and silt + 2 * clay >= 30
    ):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    if clay >= 40 and sand <= 45 and silt < 40:
        return "clay"
    return ""


def _query_hsg_polygons(wkt: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT mu.mukey, mag.hydgrpdcd, mu.mupolygongeo.STAsText() AS geom_wkt
    FROM mupolygon AS mu
    INNER JOIN muaggatt AS mag ON mu.mukey = mag.mukey
    WHERE mu.mupolygongeo.STIntersects(geometry::STGeomFromText('{wkt}', 4326)) = 1
    """
    rows = query_sda(sql, timeout=180.0)
    for row in rows:
        hsg = _resolve_hsg(row.get("hydgrpdcd"))
        row["hsg"] = hsg
        row["hsg_code"] = HSG_CODE.get(hsg, 0)
    return rows


def _query_soil_polygons(wkt: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT mu.mukey, mu.mupolygongeo.STAsText() AS geom_wkt
    FROM mupolygon AS mu
    WHERE mu.mupolygongeo.STIntersects(geometry::STGeomFromText('{wkt}', 4326)) = 1
    """
    return query_sda(sql, timeout=180.0)


def _query_horizons(mukeys: list[str], top_depth: float) -> list[dict[str, Any]]:
    keys = ",".join(f"'{key}'" for key in mukeys)
    if not keys:
        return []
    sql = f"""
    SELECT c.mukey, c.cokey, c.comppct_r, c.compname,
           ch.hzdept_r, ch.hzdepb_r, ch.sandtotal_r, ch.silttotal_r,
           ch.claytotal_r, ch.om_r
    FROM component AS c
    INNER JOIN chorizon AS ch ON ch.cokey = c.cokey
    WHERE c.mukey IN ({keys}) AND ch.hzdept_r < {top_depth}
    """
    return query_sda(sql, timeout=180.0)


def _topsoil_by_mukey(rows: list[dict[str, Any]], top_depth: float) -> dict[str, dict[str, Any]]:
    comps: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        mukey = str(row.get("mukey", ""))
        cokey = str(row.get("cokey", ""))
        comp = comps.setdefault(
            (mukey, cokey),
            {"pct": _num(row.get("comppct_r")) or 0.0, "name": row.get("compname") or "", "hz": []},
        )
        top = _num(row.get("hzdept_r"))
        bottom = _num(row.get("hzdepb_r"))
        if top is None or bottom is None or bottom <= top:
            continue
        comp["hz"].append(
            (
                top,
                bottom,
                _num(row.get("sandtotal_r")),
                _num(row.get("silttotal_r")),
                _num(row.get("claytotal_r")),
                _num(row.get("om_r")),
            )
        )
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for (mukey, cokey), comp in comps.items():
        current = best.get(mukey)
        if current is None or (-comp["pct"], _cokey_sort_key(cokey)) < (
            -current[1]["pct"],
            _cokey_sort_key(current[0]),
        ):
            best[mukey] = (cokey, comp)
    out: dict[str, dict[str, Any]] = {}
    for mukey, (_cokey, comp) in best.items():
        acc = [0.0, 0.0, 0.0, 0.0]
        weights = [0.0, 0.0, 0.0, 0.0]
        for top, bottom, sand, silt, clay, om in comp["hz"]:
            thickness = min(bottom, top_depth) - top
            if thickness <= 0:
                continue
            for index, value in enumerate((sand, silt, clay, om)):
                if value is not None:
                    acc[index] += thickness * value
                    weights[index] += thickness
        sand, silt, clay, om = [acc[i] / weights[i] if weights[i] else None for i in range(4)]
        if None in (sand, silt, clay):
            texture = ""
            code = 0
        else:
            total = sand + silt + clay
            if total > 0:
                sand, silt, clay = (100.0 * sand / total, 100.0 * silt / total, 100.0 * clay / total)
            texture = _usda_texture(sand, silt, clay)
            code = TEXTURE_CODE.get(texture, 0)
        out[mukey] = {
            "compname": comp["name"],
            "comppct": comp["pct"],
            "sand": sand,
            "silt": silt,
            "clay": clay,
            "om": om,
            "texture": texture,
            "texture_code": code,
        }
    return out


def retrieve_soil_groups(
    root: str | Path,
    site: str,
    *,
    buffer: float = 5000.0,
    pixel_size: float = 0.0003,
    center: tuple[float, float] | None = None,
) -> SoilRetrievalResult:
    _validate_positive("pixel_size", pixel_size)
    root_path = Path(root).expanduser().resolve()
    soils_dir = site_soils_dir(root_path, site)
    wkt = bbox_wkt(_query_bounds(root_path, site, buffer, center))
    rows = _query_hsg_polygons(wkt)
    vector = soils_dir / "hydrologic_soil_groups.gpkg"
    raster = soils_dir / "hsg.tif"
    _write_vector(vector, "hsg", rows)
    _rasterize(raster, vector, "hsg_code", pixel_size=pixel_size, dtype="uint8", nodata=255)
    return SoilRetrievalResult(soils_dir, vector, (raster,), len(rows))


def retrieve_hydrologic_soil_groups(
    root: str | Path,
    site: str,
    *,
    buffer: float = 5000.0,
    pixel_size: float = 0.0003,
    center: tuple[float, float] | None = None,
) -> SoilRetrievalResult:
    kwargs: dict[str, Any] = {"buffer": buffer, "pixel_size": pixel_size}
    if center is not None:
        kwargs["center"] = center
    return retrieve_soil_groups(root, site, **kwargs)


def retrieve_soil_texture(
    root: str | Path,
    site: str,
    *,
    buffer: float = 5000.0,
    pixel_size: float = 0.0003,
    top_depth: float = 30.0,
    center: tuple[float, float] | None = None,
) -> SoilRetrievalResult:
    _validate_positive("pixel_size", pixel_size)
    _validate_positive("top_depth", top_depth)
    root_path = Path(root).expanduser().resolve()
    soils_dir = site_soils_dir(root_path, site)
    wkt = bbox_wkt(_query_bounds(root_path, site, buffer, center))
    polygon_rows = _query_soil_polygons(wkt)
    mukeys = sorted({str(row["mukey"]) for row in polygon_rows if row.get("mukey") is not None})
    topsoil: dict[str, dict[str, Any]] = {}
    for index in range(0, len(mukeys), MUKEY_CHUNK):
        chunk = mukeys[index : index + MUKEY_CHUNK]
        topsoil.update(_topsoil_by_mukey(_query_horizons(chunk, top_depth), top_depth))
    rows: list[dict[str, Any]] = []
    for row in polygon_rows:
        mukey = str(row.get("mukey", ""))
        texture = topsoil.get(mukey)
        attrs = dict(row)
        if texture is None or texture["texture_code"] == 0:
            attrs.update(
                {
                    "compname": "" if texture is None else texture["compname"],
                    "comppct": None if texture is None else texture["comppct"],
                    "sand": NODATA_PCT,
                    "silt": NODATA_PCT,
                    "clay": NODATA_PCT,
                    "om": NODATA_PCT,
                    "texture": "",
                    "texture_code": 0,
                }
            )
        else:
            attrs.update(texture)
            if attrs["om"] is None:
                attrs["om"] = NODATA_PCT
        rows.append(attrs)
    vector = soils_dir / "soil_texture.gpkg"
    rasters = tuple(soils_dir / name for name in ("texture_code.tif", "sand_pct.tif", "silt_pct.tif", "clay_pct.tif"))
    _write_vector(vector, "texture", rows)
    _rasterize(rasters[0], vector, "texture_code", pixel_size=pixel_size, dtype="uint8", nodata=255)
    for raster, attribute in zip(rasters[1:], ("sand", "silt", "clay")):
        _rasterize(raster, vector, attribute, pixel_size=pixel_size, dtype="float32", nodata=NODATA_PCT)
    return SoilRetrievalResult(soils_dir, vector, rasters, len(rows))

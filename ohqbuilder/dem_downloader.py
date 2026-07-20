from __future__ import annotations

import csv
import json
import math
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal

ProductKey = Literal["dem", "demlr", "hydro", "roads", "landcover", "atlas14"]

TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
CENSUS_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
TIGER_BASE_URL = "https://www2.census.gov/geo/tiger"
MRLC_WCS_URL = "https://dmsdata.cr.usgs.gov/geoserver/mrlc_Land-Cover-Native_conus_year_data/wcs"
MRLC_COVERAGE_ID = "mrlc_Land-Cover-Native_conus_year_data:Land-Cover-Native_conus_year_data"
ATLAS14_URL = "https://hdsc.nws.noaa.gov/cgi-bin/hdsc/new/cgi_readH5.py"
METERS_PER_DEGREE = 111_320.0
DEFAULT_MAX_FILE_SIZE_MB = 512.0
DEFAULT_DEM_RESOLUTION = "1/3"
NLCD_PIXEL_M = 30.0
NLCD_GRID_OFF = 15.0


@dataclass(frozen=True)
class ProductTier:
    dataset: str
    formats: tuple[str, ...]
    resolution_label: str


@dataclass(frozen=True)
class DownloadItem:
    title: str
    url: str
    dataset: str
    resolution: str
    publication_date: str = ""
    size_bytes: int | None = None


@dataclass(frozen=True)
class SiteDownloadResult:
    site_id: str
    product: ProductKey
    status: str
    count: int
    downloaded: int
    output_dir: Path | None
    best_dataset: str = ""
    best_resolution: str = ""
    url: str = ""
    date: str = ""


ELEVATION_TIERS: tuple[ProductTier, ...] = (
    ProductTier("Digital Elevation Model (DEM) 1 meter", ("GeoTIFF",), "1 m"),
    ProductTier("National Elevation Dataset (NED) 1/9 arc-second", ("GeoTIFF",), "1/9 arc-second"),
    ProductTier("National Elevation Dataset (NED) 1/3 arc-second", ("GeoTIFF",), "1/3 arc-second"),
    ProductTier("National Elevation Dataset (NED) 1 arc-second", ("GeoTIFF",), "1 arc-second"),
)
LOW_RES_ELEVATION_TIERS: tuple[ProductTier, ...] = (
    ProductTier("National Elevation Dataset (NED) 1/3 arc-second", ("GeoTIFF",), "1/3 arc-second"),
)
HYDRO_TIERS: tuple[ProductTier, ...] = (
    ProductTier(
        "National Hydrography Dataset Plus High Resolution (NHDPlus HR)",
        ("Shapefile", "FileGDB"),
        "NHDPlus HR",
    ),
    ProductTier(
        "National Hydrography Dataset (NHD) Best Resolution",
        ("Shapefile", "FileGDB"),
        "NHD Best Resolution",
    ),
)
PRODUCT_TIERS: dict[ProductKey, tuple[ProductTier, ...]] = {
    "dem": ELEVATION_TIERS,
    "demlr": LOW_RES_ELEVATION_TIERS,
    "hydro": HYDRO_TIERS,
}
DEFAULT_MAX_TILES: dict[ProductKey, int] = {
    "dem": 8,
    "demlr": 8,
    "hydro": 4,
    "roads": 1,
    "landcover": 1,
    "atlas14": 1,
}
ATLAS14_DURATIONS = (
    "5min", "10min", "15min", "30min", "60min", "2hr", "3hr", "6hr", "12hr", "24hr",
    "2day", "3day", "4day", "7day", "10day", "20day", "30day", "45day", "60day",
)
ATLAS14_RETURN_PERIODS = ("2yr", "5yr", "10yr", "25yr", "50yr", "100yr")


def parse_products(value: str) -> list[ProductKey]:
    selected = [part.strip().lower() for part in value.split(",") if part.strip()]
    if "all" in selected:
        return ["dem", "demlr", "hydro", "roads", "landcover", "atlas14"]
    products: list[ProductKey] = []
    for key in selected:
        if key == "demhr":
            key = "dem"
        if key == "nlcd":
            key = "landcover"
        if key not in {"dem", "demlr", "hydro", "roads", "landcover", "atlas14"}:
            raise ValueError(
                "products must be 'dem' (or 'demhr'), 'demlr', 'hydro', 'roads', "
                "'landcover' (or 'nlcd'), 'atlas14', 'all', or a comma-separated subset"
            )
        products.append(key)  # type: ignore[arg-type]
    return products


def _detect_column(headers: Iterable[str], explicit: str | None, candidates: tuple[str, ...], label: str) -> str:
    names = list(headers)
    if explicit:
        if explicit not in names:
            raise ValueError(f"{label} column not found: {explicit}")
        return explicit
    lowered = {name.lower().replace("_", "").replace(" ", ""): name for name in names}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    raise ValueError(f"Could not auto-detect {label} column; pass --{label}-col")


def _site_id(row: dict[str, str], id_col: str | None, index: int) -> str:
    raw = row.get(id_col, "") if id_col else ""
    value = raw.strip() or f"site_{index + 1}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or f"site_{index + 1}"


def _bbox(lon: float, lat: float, buffer_m: float) -> str:
    dlat = buffer_m / METERS_PER_DEGREE
    dlon = buffer_m / (METERS_PER_DEGREE * max(0.1, abs(math.cos(math.radians(lat)))))
    return f"{lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}"


def query_tnm(lon: float, lat: float, tier: ProductTier, buffer_m: float, timeout: float = 60.0) -> list[DownloadItem]:
    params = {"datasets": tier.dataset, "bbox": _bbox(lon, lat, buffer_m), "outputFormat": "JSON", "max": "100"}
    if tier.formats:
        params["prodFormats"] = ",".join(tier.formats)
    url = f"{TNM_PRODUCTS_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        data = json.loads(response.read().decode("utf-8"))
    results: list[DownloadItem] = []
    for item in data.get("items") or []:
        urls = item.get("urls") or {}
        download_url = urls.get("TIFF") or urls.get("Shapefile") or urls.get("GeoPackage") or urls.get("FileGDB") or item.get("downloadURL") or item.get("downloadUrl") or item.get("url")
        if download_url:
            raw_size = item.get("sizeInBytes")
            try:
                size = int(raw_size) if raw_size not in (None, "") else None
            except (TypeError, ValueError):
                size = None
            results.append(DownloadItem(item.get("title") or Path(download_url).name, download_url, tier.dataset, tier.resolution_label, item.get("publicationDate") or item.get("dateCreated") or "", size))
    return results


def _filename_from_url(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name or fallback
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _item_date_key(item: DownloadItem) -> str:
    text = " ".join((item.publication_date, item.title, item.url))
    matches = re.findall(r"(?:19|20)\d{6}", text)
    if matches:
        return max(matches)
    year_matches = re.findall(r"(?:19|20)\d{2}", text)
    return max(year_matches) if year_matches else ""


def _tile_key(item: DownloadItem, product: ProductKey) -> str:
    text = f"{item.title} {_filename_from_url(item.url, item.title)}"
    if product in {"dem", "demlr"}:
        match = re.search(r"[ns]\d{2}[ew]\d{3}", text, re.IGNORECASE)
        if match:
            return match.group(0).lower()
        match = re.search(r"x\d+y\d+", text, re.IGNORECASE)
        if match:
            return match.group(0).lower()
    return _filename_from_url(item.url, item.title)


def _dedupe_latest_by_tile(items: list[DownloadItem], product: ProductKey) -> list[DownloadItem]:
    latest: dict[str, DownloadItem] = {}
    for item in items:
        key = _tile_key(item, product)
        current = latest.get(key)
        if current is None or _item_date_key(item) >= _item_date_key(current):
            latest[key] = item
    return sorted(latest.values(), key=lambda item: (_tile_key(item, product), item.title, item.url))


def _hydro_hu4_code(item: DownloadItem) -> str | None:
    text = f"{item.title} {_filename_from_url(item.url, item.title)}"
    match = re.search(r"H_(\d{4})_HU4", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"HU4[_-]?(\d{4})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _hydro_hu4_key(item: DownloadItem) -> str:
    return _hydro_hu4_code(item) or _filename_from_url(item.url, item.title)


def _is_hydro_raster_package(item: DownloadItem) -> bool:
    text = f"{item.title} {_filename_from_url(item.url, item.title)}"
    return bool(re.search(r"(?:^|[_-])raster(?:[_\.-]|$)", text, re.IGNORECASE))


def _hydro_vector_rank(item: DownloadItem) -> int:
    text = f"{item.title} {_filename_from_url(item.url, item.title)}".lower()
    if "gdb" in text or "geodatabase" in text:
        return 0
    if "shp" in text or "shape" in text or "shapefile" in text:
        return 1
    return 2


def _prefer_hydro_packages(items: list[DownloadItem]) -> list[DownloadItem]:
    vector_items = [item for item in items if not _is_hydro_raster_package(item)]
    hu4_items = [item for item in vector_items if _hydro_hu4_code(item)]
    if hu4_items:
        vector_items = hu4_items
    latest: dict[str, DownloadItem] = {}
    for item in vector_items:
        key = _hydro_hu4_key(item)
        current = latest.get(key)
        if current is None:
            latest[key] = item
            continue
        current_score = (_item_date_key(current), -_hydro_vector_rank(current), current.title, current.url)
        item_score = (_item_date_key(item), -_hydro_vector_rank(item), item.title, item.url)
        if item_score > current_score:
            latest[key] = item
    return sorted(latest.values(), key=lambda item: (item.size_bytes if item.size_bytes is not None else 10**18, _hydro_hu4_key(item), item.title, item.url))


def download_file(url: str, destination: Path, timeout: float = 120.0, expected_size: int | None = None) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        actual_size = destination.stat().st_size
        if actual_size > 0 and (expected_size is None or actual_size == expected_size):
            return False
        destination.unlink()
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as tmp:
            shutil.copyfileobj(response, tmp)
            tmp_path = Path(tmp.name)
    tmp_path.replace(destination)
    return True


def county_fips_for_point(lat: float, lon: float, timeout: float = 30.0) -> tuple[str, str]:
    params = {
        "x": f"{lon:.8f}", "y": f"{lat:.8f}", "benchmark": "Public_AR_Current",
        "vintage": "Current_Current", "layers": "Counties", "format": "json",
    }
    url = f"{CENSUS_GEOCODER_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        data = json.loads(response.read().decode("utf-8"))
    counties = data.get("result", {}).get("geographies", {}).get("Counties", [])
    if not counties:
        raise ValueError("no county found for point")
    county = counties[0]
    geoid = county.get("GEOID") or f"{county.get('STATE', '')}{county.get('COUNTY', '')}"
    if len(geoid) != 5:
        raise ValueError(f"unexpected county GEOID: {geoid!r}")
    county_name = ", ".join(part for part in (county.get("NAME"), county.get("STUSAB")) if part)
    return geoid, county_name


def roads_url_for_fips(fips: str, tiger_year: int) -> str:
    return f"{TIGER_BASE_URL}/TIGER{tiger_year}/ROADS/tl_{tiger_year}_{fips}_roads.zip"


def _albers_q(sin_phi: float) -> float:
    e2 = 0.00669438002290
    e = math.sqrt(e2)
    es = e2 * sin_phi * sin_phi
    return (1.0 - e2) * (sin_phi / (1.0 - es) - (1.0 / (2.0 * e)) * math.log((1.0 - e * sin_phi) / (1.0 + e * sin_phi)))


def _albers_m(sin_phi: float, cos_phi: float) -> float:
    return cos_phi / math.sqrt(1.0 - 0.00669438002290 * sin_phi * sin_phi)


def lonlat_to_albers(lon: float, lat: float) -> tuple[float, float]:
    a = 6378137.0
    p1, p2, p0, l0 = map(math.radians, (29.5, 45.5, 23.0, -96.0))
    p, lambda_value = math.radians(lat), math.radians(lon)
    m1 = _albers_m(math.sin(p1), math.cos(p1))
    m2 = _albers_m(math.sin(p2), math.cos(p2))
    q1 = _albers_q(math.sin(p1))
    q2 = _albers_q(math.sin(p2))
    q0 = _albers_q(math.sin(p0))
    q = _albers_q(math.sin(p))
    n = (m1 * m1 - m2 * m2) / (q2 - q1)
    c = m1 * m1 + n * q1
    rho = a * math.sqrt(c - n * q) / n
    rho0 = a * math.sqrt(c - n * q0) / n
    theta = n * (lambda_value - l0)
    return rho * math.sin(theta), rho0 - rho * math.cos(theta)


def _snap_to_grid(value: float, up: bool) -> float:
    k = (value - NLCD_GRID_OFF) / NLCD_PIXEL_M
    n = math.ceil(k) if up else math.floor(k)
    return n * NLCD_PIXEL_M + NLCD_GRID_OFF


def landcover_url(lat: float, lon: float, buffer_m: float, nlcd_year: int) -> str:
    cx, cy = lonlat_to_albers(lon, lat)
    bbox = (
        _snap_to_grid(cx - buffer_m, False), _snap_to_grid(cy - buffer_m, False),
        _snap_to_grid(cx + buffer_m, True), _snap_to_grid(cy + buffer_m, True),
    )
    params = {
        "service": "WCS", "version": "1.0.0", "request": "GetCoverage",
        "coverage": MRLC_COVERAGE_ID, "format": "GeoTIFF", "crs": "EPSG:5070",
        "bbox": ",".join(f"{v:.3f}" for v in bbox), "resx": "30.0", "resy": "30.0",
        "time": f"{nlcd_year}-01-01T00:00:00.000Z",
    }
    return f"{MRLC_WCS_URL}?{urllib.parse.urlencode(params)}"


def _parse_js_values(source: str, var_name: str) -> list[str]:
    start = source.find(f"{var_name} = [")
    if start < 0:
        return []
    i = start + len(var_name) + 3
    depth = 0
    values: list[str] = []
    while i < len(source):
        char = source[i]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                break
        elif char == "'":
            j = source.find("'", i + 1)
            if j < 0:
                break
            values.append(source[i + 1:j])
            i = j
        i += 1
    return values


def query_atlas14(lat: float, lon: float, timeout: float = 60.0) -> dict[str, dict[str, float]]:
    params = {"aoi": "point", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "type": "pf", "data": "depth", "units": "english", "series": "pd"}
    url = f"{ATLAS14_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        text = response.read().decode("utf-8", errors="replace")
    values = _parse_js_values(text, "quantiles")
    if not values:
        raise ValueError("Atlas 14 response did not include quantiles")
    table: dict[str, dict[str, float]] = {}
    width = len(ATLAS14_DURATIONS)
    for rp_index, rp in enumerate(ATLAS14_RETURN_PERIODS):
        for dur_index, duration in enumerate(ATLAS14_DURATIONS):
            value_index = rp_index * width + dur_index
            if value_index >= len(values):
                continue
            try:
                value = float(values[value_index])
            except ValueError:
                continue
            table.setdefault(duration, {})[rp] = value
    return table


def write_atlas14_csv(path: Path, table: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["duration", *ATLAS14_RETURN_PERIODS])
        for duration in ATLAS14_DURATIONS:
            if duration in table:
                writer.writerow([duration, *[f"{table[duration].get(rp, ''):.2f}" if rp in table[duration] else "" for rp in ATLAS14_RETURN_PERIODS]])


def _dbf_field_name(name: str, index: int, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")[:10] or f"F{index}"
    candidate = base
    suffix = 1
    while candidate.lower() in used:
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 10 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def write_point_shapefile(path: Path, row: dict[str, str], lon: float, lat: float, lat_name: str, lon_name: str) -> Path:
    import geopandas as gpd
    from shapely.geometry import Point

    used: set[str] = set()
    attrs = {
        _dbf_field_name(key, index, used): value
        for index, (key, value) in enumerate(row.items())
        if key not in {lat_name, lon_name}
    }
    gdf = gpd.GeoDataFrame([attrs], geometry=[Point(lon, lat)], crs="EPSG:4326")
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path)
    return path


def _tnm_product_result(
    product: ProductKey,
    site: str,
    lat: float,
    lon: float,
    out_base: Path | None,
    buffer_m: float,
    max_tiles: int | None,
    max_file_size_mb: float | None,
    progress: Callable[[str], None] | None,
    dem_resolution: str = DEFAULT_DEM_RESOLUTION,
) -> SiteDownloadResult:
    size_limit = None if max_file_size_mb in (None, 0) else int(max_file_size_mb * 1024 * 1024)
    cap = DEFAULT_MAX_TILES[product] if max_tiles is None else max_tiles
    high_res_dem_limit = DEFAULT_MAX_TILES["dem"]
    best_found: list[DownloadItem] = []
    best_allowed: list[DownloadItem] = []

    tiers = PRODUCT_TIERS[product]
    if product == "dem" and dem_resolution != "auto":
        aliases = {"1m": "1 m", "1": "1 m", "1/9": "1/9 arc-second", "1/3": "1/3 arc-second", "10m": "1/3 arc-second", "30m": "1 arc-second"}
        wanted = aliases.get(dem_resolution.lower(), dem_resolution)
        tiers = tuple(tier for tier in tiers if tier.resolution_label == wanted) or tiers

    for tier in tiers:
        if progress:
            progress(f"Querying {product} {tier.resolution_label} products for {site}...")
        found = query_tnm(lon, lat, tier, buffer_m)
        if product == "hydro":
            found = sorted(found, key=lambda item: item.size_bytes if item.size_bytes is not None else 10**18)
            deduped = _prefer_hydro_packages(found)
        else:
            deduped = _dedupe_latest_by_tile(found, product) if product in {"dem", "demlr"} else found
        allowed = [
            item
            for item in deduped
            if size_limit is None or item.size_bytes is None or item.size_bytes <= size_limit
        ]
        if found:
            best_found = found
            best_allowed = allowed
        if product == "dem" and tier.resolution_label == "1 m" and len(allowed) > high_res_dem_limit:
            if progress:
                progress(
                    f"Skipping 1 m DEM for {site}: {len(allowed)} unique tile(s) after latest-version filtering; "
                    f"using a coarser seamless tier instead."
                )
            continue
        if allowed:
            selected = allowed if cap == 0 else allowed[:cap]
            return _download_selected_product(product, site, selected, found, allowed, out_base, progress)

    selected = best_allowed if cap == 0 else best_allowed[:cap]
    return _download_selected_product(product, site, selected, best_found, best_allowed, out_base, progress)


def _download_selected_product(
    product: ProductKey,
    site: str,
    selected: list[DownloadItem],
    found: list[DownloadItem],
    allowed: list[DownloadItem],
    out_base: Path | None,
    progress: Callable[[str], None] | None,
) -> SiteDownloadResult:
    product_dir = out_base / site / product if out_base else None
    downloaded = 0
    if progress:
        candidate_label = "preferred unique/latest vector package" if product == "hydro" else "unique/latest"
        progress(
            f"Found {len(found)} {product} candidate(s); {len(allowed)} {candidate_label} under size limit; "
            f"downloading {len(selected)}."
        )
    if product_dir:
        for item_index, item in enumerate(selected, start=1):
            name = _filename_from_url(item.url, f"{product}_{item_index}.dat")
            destination = product_dir / name
            if progress:
                progress(f"Downloading {product} {item_index}/{len(selected)}: {name}")
            if destination.exists() and item.size_bytes is not None and destination.stat().st_size != item.size_bytes:
                if progress:
                    progress(f"Existing {product} file is incomplete/corrupt; redownloading: {name}")
            if download_file(item.url, destination, expected_size=item.size_bytes):
                downloaded += 1
    return SiteDownloadResult(
        site,
        product,
        "ok" if selected else ("too large" if found else "no coverage"),
        len(found),
        downloaded,
        product_dir,
        selected[0].dataset if selected else "",
        selected[0].resolution if selected else "",
        selected[0].url if selected else "",
        selected[0].publication_date if selected else "",
    )


def _special_product_result(
    product: ProductKey,
    site: str,
    lat: float,
    lon: float,
    out_base: Path | None,
    buffer_m: float,
    tiger_year: int,
    nlcd_year: int,
    progress: Callable[[str], None] | None,
) -> SiteDownloadResult:
    product_dir = out_base / site / product if out_base else None
    downloaded = 0
    status = "ok"
    count = 1
    best_dataset = ""
    best_resolution = ""
    url = ""
    date = ""
    if product == "roads":
        if progress:
            progress(f"Resolving TIGER/Line roads for {site}...")
        try:
            fips, county_name = county_fips_for_point(lat, lon)
            url = roads_url_for_fips(fips, tiger_year)
            best_dataset = "Census TIGER/Line All Roads"
            best_resolution = county_name or fips
            date = str(tiger_year)
            if product_dir:
                destination = product_dir / _filename_from_url(url, f"tl_{tiger_year}_{fips}_roads.zip")
                if download_file(url, destination, expected_size=None):
                    downloaded = 1
        except Exception as exc:  # network/product failures should be reflected in CSV status
            status = f"error: {exc}"
            count = 0
    elif product == "landcover":
        if progress:
            progress(f"Downloading NLCD land cover for {site}...")
        url = landcover_url(lat, lon, buffer_m, nlcd_year)
        best_dataset = f"NLCD Annual Land Cover {nlcd_year} (MRLC WCS)"
        best_resolution = "30 m"
        date = str(nlcd_year)
        if product_dir:
            destination = product_dir / f"nlcd_{nlcd_year}_{site}.tif"
            try:
                if download_file(url, destination, timeout=300.0, expected_size=None):
                    downloaded = 1
            except Exception as exc:
                status = f"error: {exc}"
                count = 0
    elif product == "atlas14":
        if progress:
            progress(f"Querying NOAA Atlas 14 for {site}...")
        try:
            table = query_atlas14(lat, lon)
            best_dataset = "NOAA Atlas 14 precipitation frequency estimates"
            best_resolution = "partial-duration depth"
            url = f"{ATLAS14_URL}?{urllib.parse.urlencode({'aoi': 'point', 'lat': f'{lat:.6f}', 'lon': f'{lon:.6f}', 'type': 'pf', 'data': 'depth', 'units': 'english', 'series': 'pd'})}"
            if product_dir:
                write_atlas14_csv(product_dir / "atlas14_pf.csv", table)
                downloaded = 1
        except Exception as exc:
            status = f"error: {exc}"
            count = 0
    return SiteDownloadResult(site, product, status, count, downloaded, product_dir, best_dataset, best_resolution, url, date)


def process_csv(
    input_csv: str | Path,
    output_csv: str | Path | None,
    *,
    products: list[ProductKey],
    download_dir: str | Path | None = None,
    id_col: str | None = None,
    lat_col: str | None = None,
    lon_col: str | None = None,
    buffer_m: float = 30.0,
    max_tiles: int | None = None,
    max_file_size_mb: float | None = DEFAULT_MAX_FILE_SIZE_MB,
    dem_resolution: str = DEFAULT_DEM_RESOLUTION,
    make_points: bool = False,
    points_dir: str | Path | None = None,
    tiger_year: int = 2025,
    nlcd_year: int = 2023,
    progress: Callable[[str], None] | None = None,
) -> list[SiteDownloadResult]:
    input_path = Path(input_csv)
    rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8-sig")))
    headers = rows[0].keys() if rows else []
    lat_name = _detect_column(headers, lat_col, ("lat", "latitude", "centroidlat", "sitelat", "y"), "lat")
    lon_name = _detect_column(headers, lon_col, ("lon", "lng", "long", "longitude", "centroidlon", "sitelon", "x"), "lon")
    out_base = Path(download_dir) if download_dir else None
    point_base = Path(points_dir) if points_dir else out_base
    results: list[SiteDownloadResult] = []
    output_rows: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        site = _site_id(row, id_col, index)
        output_row = dict(row)
        try:
            lat = float(row[lat_name])
            lon = float(row[lon_name])
        except ValueError:
            for product in products:
                result = SiteDownloadResult(site, product, "missing/invalid coordinate", 0, 0, None)
                results.append(result)
                _append_result(output_row, result, bool(out_base))
            if make_points:
                output_row["point_dir"] = ""
            output_rows.append(output_row)
            continue
        for product in products:
            if product in PRODUCT_TIERS:
                result = _tnm_product_result(product, site, lat, lon, out_base, buffer_m, max_tiles, max_file_size_mb, progress, dem_resolution)
            else:
                result = _special_product_result(product, site, lat, lon, out_base, buffer_m, tiger_year, nlcd_year, progress)
            results.append(result)
            _append_result(output_row, result, bool(out_base))
        if make_points:
            point_dir = point_base / site / "point" if point_base else Path(".") / site / "point"
            point_path = point_dir / f"{site}.shp"
            write_point_shapefile(point_path, row, lon, lat, lat_name, lon_name)
            output_row["point_dir"] = str(point_dir)
        output_rows.append(output_row)
    if output_csv:
        _write_output_csv(Path(output_csv), output_rows)
    return results


def _append_result(row: dict[str, str], result: SiteDownloadResult, include_download: bool) -> None:
    prefix = result.product
    row[f"{prefix}_best_resolution"] = result.best_resolution
    row[f"{prefix}_best_dataset"] = result.best_dataset
    row[f"{prefix}_count"] = str(result.count)
    row[f"{prefix}_date"] = result.date
    row[f"{prefix}_url"] = result.url
    row[f"{prefix}_status"] = result.status
    if include_download:
        row[f"{prefix}_downloaded"] = str(result.downloaded)
        row[f"{prefix}_dir"] = str(result.output_dir or "")


def _write_output_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

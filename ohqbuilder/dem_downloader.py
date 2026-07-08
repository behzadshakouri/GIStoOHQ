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
from typing import Iterable, Literal

ProductKey = Literal["dem", "hydro"]

TNM_PRODUCTS_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
METERS_PER_DEGREE = 111_320.0


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


ELEVATION_TIERS: tuple[ProductTier, ...] = (
    ProductTier("Digital Elevation Model (DEM) 1 meter", ("GeoTIFF",), "1 m"),
    ProductTier("National Elevation Dataset (NED) 1/9 arc-second", ("GeoTIFF",), "1/9 arc-second"),
    ProductTier("National Elevation Dataset (NED) 1/3 arc-second", ("GeoTIFF",), "1/3 arc-second"),
    ProductTier("National Elevation Dataset (NED) 1 arc-second", ("GeoTIFF",), "1 arc-second"),
)
HYDRO_TIERS: tuple[ProductTier, ...] = (
    ProductTier("National Hydrography Dataset Plus High Resolution (NHDPlus HR)", ("Shapefile", "FileGDB"), "NHDPlus HR"),
    ProductTier("National Hydrography Dataset (NHD) Best Resolution", ("Shapefile", "FileGDB"), "NHD Best Resolution"),
)
PRODUCT_TIERS: dict[ProductKey, tuple[ProductTier, ...]] = {"dem": ELEVATION_TIERS, "hydro": HYDRO_TIERS}
DEFAULT_MAX_TILES: dict[ProductKey, int] = {"dem": 8, "hydro": 4}


def parse_products(value: str) -> list[ProductKey]:
    if value == "all":
        return ["dem", "hydro"]
    products: list[ProductKey] = []
    for part in value.split(","):
        key = part.strip().lower()
        if key not in {"dem", "hydro"}:
            raise ValueError("products must be 'dem', 'hydro', 'all', or a comma-separated subset")
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
    params = {
        "datasets": tier.dataset,
        "bbox": _bbox(lon, lat, buffer_m),
        "outputFormat": "JSON",
        "max": "100",
    }
    if tier.formats:
        params["prodFormats"] = ",".join(tier.formats)
    url = f"{TNM_PRODUCTS_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS API URL
        data = json.loads(response.read().decode("utf-8"))
    items = data.get("items") or []
    results: list[DownloadItem] = []
    for item in items:
        download_url = item.get("downloadURL") or item.get("downloadUrl") or item.get("url")
        if download_url:
            results.append(DownloadItem(item.get("title") or Path(download_url).name, download_url, tier.dataset, tier.resolution_label))
    return results


def _filename_from_url(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(parsed.path)).name or fallback
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def download_file(url: str, destination: Path, timeout: float = 120.0) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return False
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - URL comes from TNM API
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as tmp:
            shutil.copyfileobj(response, tmp)
            tmp_path = Path(tmp.name)
    tmp_path.replace(destination)
    return True


def process_csv(input_csv: str | Path, output_csv: str | Path | None, *, products: list[ProductKey], download_dir: str | Path | None = None, id_col: str | None = None, lat_col: str | None = None, lon_col: str | None = None, buffer_m: float = 30.0, max_tiles: int | None = None) -> list[SiteDownloadResult]:
    input_path = Path(input_csv)
    rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8-sig")))
    headers = rows[0].keys() if rows else []
    lat_name = _detect_column(headers, lat_col, ("lat", "latitude", "y"), "lat")
    lon_name = _detect_column(headers, lon_col, ("lon", "lng", "long", "longitude", "x"), "lon")
    out_base = Path(download_dir) if download_dir else None
    results: list[SiteDownloadResult] = []
    for index, row in enumerate(rows):
        site = _site_id(row, id_col, index)
        try:
            lat = float(row[lat_name])
            lon = float(row[lon_name])
        except ValueError:
            continue
        for product in products:
            found: list[DownloadItem] = []
            for tier in PRODUCT_TIERS[product]:
                found = query_tnm(lon, lat, tier, buffer_m)
                if found:
                    break
            cap = DEFAULT_MAX_TILES[product] if max_tiles is None else max_tiles
            selected = found if cap == 0 else found[:cap]
            product_dir = out_base / site / product if out_base else None
            downloaded = 0
            if product_dir:
                for item_index, item in enumerate(selected):
                    name = _filename_from_url(item.url, f"{product}_{item_index + 1}.dat")
                    if download_file(item.url, product_dir / name):
                        downloaded += 1
            results.append(SiteDownloadResult(site, product, "ok" if found else "no coverage", len(found), downloaded, product_dir, selected[0].dataset if selected else "", selected[0].resolution if selected else "", selected[0].url if selected else ""))
    if output_csv:
        with Path(output_csv).open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=["site_id", "product", "status", "count", "downloaded", "dir", "best_dataset", "best_resolution", "url"])
            writer.writeheader()
            for r in results:
                writer.writerow({"site_id": r.site_id, "product": r.product, "status": r.status, "count": r.count, "downloaded": r.downloaded, "dir": str(r.output_dir or ""), "best_dataset": r.best_dataset, "best_resolution": r.best_resolution, "url": r.url})
    return results

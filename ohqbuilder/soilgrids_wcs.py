"""Download bounded SoilGrids texture predictions without inferring HSG."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request


WCS_URL = "https://maps.isric.org/mapserv"
CRS_URL = "http://www.opengis.net/def/crs/EPSG/0/152160"
MAX_BYTES = 100 * 1024 * 1024
DEPTHS = ("0-5cm", "5-15cm", "15-30cm")


def coverage_url(property_name: str, depth: str, bounds: tuple[float, float, float, float]) -> str:
    """Construct the documented WCS 2.0.1 GetCoverage request in native CRS."""

    if property_name not in {"sand", "silt", "clay"} or depth not in DEPTHS:
        raise ValueError("Choose sand/silt/clay and a supported SoilGrids depth")
    xmin, ymin, xmax, ymax = bounds
    if not all(math.isfinite(v) for v in bounds) or not xmin < xmax or not ymin < ymax:
        raise ValueError("Projected SoilGrids bounds must be finite and ordered")
    if xmax - xmin > 300_000 or ymax - ymin > 300_000:
        raise ValueError("SoilGrids WCS subset exceeds 300 km per side; split the request")
    def coordinate(value: float) -> str:
        return f"{value:.3f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    parameters = [
        ("map", f"/map/{property_name}.map"), ("SERVICE", "WCS"),
        ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"),
        ("COVERAGEID", f"{property_name}_{depth}_Q0.5"),
        ("FORMAT", "GEOTIFF_INT16"),
        ("SUBSET", f"X({coordinate(xmin)},{coordinate(xmax)})"),
        ("SUBSET", f"Y({coordinate(ymin)},{coordinate(ymax)})"),
        ("SUBSETTINGCRS", CRS_URL), ("OUTPUTCRS", CRS_URL),
    ]
    return WCS_URL + "?" + urllib.parse.urlencode(parameters)


def download_texture(
    bounds: tuple[float, float, float, float], depth: str, output_dir: str | Path
) -> Path:
    """Fetch raw g/kg medians for sand/silt/clay, with checksums and metadata."""

    import rasterio

    folder = Path(output_dir).expanduser().resolve()
    # Check all arguments before creating directories or requesting a service.
    urls = {property_name: coverage_url(property_name, depth, bounds)
            for property_name in ("sand", "silt", "clay")}
    folder.mkdir(parents=True, exist_ok=True)
    records = {}
    for property_name, url in urls.items():
        target = folder / f"soilgrids_{property_name}_{depth}_Q0.5.tif"
        temporary = target.with_name(target.name + ".partial")
        digest = hashlib.sha256()
        count = 0
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as sink:  # noqa: S310
                while block := response.read(1024 * 1024):
                    count += len(block)
                    if count > MAX_BYTES:
                        raise ValueError("SoilGrids WCS response exceeds the size limit")
                    sink.write(block)
                    digest.update(block)
            if count == 0:
                raise ValueError(f"Empty SoilGrids coverage: {property_name}")
            with rasterio.open(temporary) as raster:
                if raster.count != 1 or raster.crs is None or raster.width < 1 or raster.height < 1:
                    raise ValueError(f"Invalid SoilGrids coverage: {property_name}")
                for _, window in raster.block_windows(1):
                    raster.read(1, window=window)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        records[property_name] = {"path": target.name, "url": url, "sha256": digest.hexdigest()}
    manifest = folder / f"soilgrids_texture_{depth}.json"
    temporary = manifest.with_name(manifest.name + ".partial")
    try:
        temporary.write_text(json.dumps({
            "source": "ISRIC SoilGrids WCS", "depth": depth,
            "quantile": "Q0.5", "units": "g/kg", "percent_conversion": "divide by 10",
            "bounds_epsg_152160": bounds, "properties": records,
            "hsg_status": "not derived; requires independently reviewed hydrologic soil groups",
        }, indent=2) + "\n", encoding="utf-8")
        temporary.replace(manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest

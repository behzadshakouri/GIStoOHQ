from __future__ import annotations

import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .dem_downloader import ProductKey, parse_products, process_csv


class Phase1FetchError(RuntimeError):
    """Raised when phase-1 input bootstrapping cannot complete."""


@dataclass(frozen=True)
class Phase1FetchResult:
    site_path: Path
    outlet_path: Path | None
    download_dir: Path
    summary_csv: Path
    manifest_path: Path


def write_outlet_shapefile(path: str | Path, lon: float, lat: float) -> Path:
    """Write the single-feature outlet shapefile expected by legacy phase 1."""

    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError as exc:  # pragma: no cover - exercised through CLI environments
        raise Phase1FetchError(
            "Writing outputs/outlet.shp requires GIS Python dependencies. "
            "Install them with `pip install -e .[gis]` or create outlet.shp manually."
        ) from exc

    outlet_path = Path(path)
    outlet_path.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame(
        {"id": [1], "name": ["outlet"]},
        geometry=[Point(lon, lat)],
        crs="EPSG:4326",
    )
    gdf.to_file(outlet_path)
    return outlet_path


def _write_single_site_csv(path: Path, site_id: str, lon: float, lat: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["site_id", "lat", "lon"])
        writer.writeheader()
        writer.writerow({"site_id": site_id, "lat": lat, "lon": lon})


def _write_manifest(
    path: Path,
    *,
    outlet_path: Path | None,
    download_dir: Path,
    summary_csv: Path,
    products: list[ProductKey],
) -> None:
    outlet_line = (
        f"- Created `{outlet_path.relative_to(path.parent)}` from the supplied WGS84 coordinate."
        if outlet_path
        else "- Skipped outlet creation because `--skip-outlet` was used."
    )
    product_lines = "\n".join(f"- Downloaded/query summary for `{product}` source products." for product in products)
    path.write_text(
        "\n".join(
            [
                "# Phase 1 source-input bootstrap",
                "",
                outlet_line,
                product_lines,
                f"- Raw TNM downloads are under `{download_dir.relative_to(path.parent)}`.",
                f"- Download/query details are in `{summary_csv.relative_to(path.parent)}`.",
                "",
                "## Still required before `prepare-inputs`",
                "",
                "The bootstrapper downloads source products and can create the outlet point, but the legacy QGIS phase still expects these exact local files:",
                "",
                "```text",
                "demlr/cliped_utm.tif",
                "outputs/NHDFlowline_clip.gpkg",
                "outputs/outlet.shp",
                "```",
                "",
                "If `demlr/cliped_utm.tif` or `outputs/NHDFlowline_clip.gpkg` is still missing, mosaic/reproject/clip the downloaded source products into those filenames before rerunning `prepare-inputs`.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def fetch_phase1_inputs(
    root: str | Path,
    site: str,
    *,
    lon: float,
    lat: float,
    site_id: str | None = None,
    products: str = "all",
    download_dir: str | Path | None = None,
    buffer_m: float = 500.0,
    max_tiles: int | None = None,
    skip_outlet: bool = False,
) -> Phase1FetchResult:
    """Bootstrap source data for the phase-1 legacy GIS workflow.

    The function intentionally stops short of pretending raw TNM products are the
    final legacy inputs: DEM mosaicking/reprojection and hydrography extraction
    remain explicit GIS preparation tasks documented in the generated manifest.
    """

    root_path = Path(root).expanduser().resolve()
    site_path = root_path / site
    outputs_path = site_path / "outputs"
    demlr_path = site_path / "demlr"
    outputs_path.mkdir(parents=True, exist_ok=True)
    demlr_path.mkdir(parents=True, exist_ok=True)

    outlet_path = None if skip_outlet else write_outlet_shapefile(outputs_path / "outlet.shp", lon, lat)
    selected_products = parse_products(products)
    source_dir = Path(download_dir).expanduser().resolve() if download_dir else site_path / "source_downloads"
    summary_csv = site_path / "source_downloads_summary.csv"

    with tempfile.TemporaryDirectory() as tmp:
        site_csv = Path(tmp) / "site.csv"
        _write_single_site_csv(site_csv, site_id or site_path.name or "site", lon, lat)
        process_csv(
            site_csv,
            summary_csv,
            products=selected_products,
            download_dir=source_dir,
            id_col="site_id",
            lat_col="lat",
            lon_col="lon",
            buffer_m=buffer_m,
            max_tiles=max_tiles,
        )

    manifest_path = site_path / "PHASE1_INPUTS.md"
    _write_manifest(
        manifest_path,
        outlet_path=outlet_path,
        download_dir=source_dir,
        summary_csv=summary_csv,
        products=selected_products,
    )
    return Phase1FetchResult(site_path, outlet_path, source_dir, summary_csv, manifest_path)

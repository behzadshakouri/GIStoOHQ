#!/usr/bin/env python3
# =============================================================================
# retrieve_soil_groups.py
#
# Query USDA Soil Data Access (SDA) for hydrologic soil group polygons around
# each site in a CSV and write per-site GIS deliverables.
#
# Outputs per site:
#   <out-root>/<site>/soils/<site>_soils.json
#   <out-root>/<site>/soils/hydrologic_soil_groups.gpkg
#   <out-root>/<site>/soils/hsg.tif
#
# Example:
#   python3 retrieve_soil_groups.py \
#       --root /path/to/project \
#       --csv WS3_GIS/ws3_site_locations.csv \
#       --out-rel WS3_GIS \
#       --id-col project_no --lat-col lat --lon-col lon
#
# A JSON config can also be used:
#   python3 retrieve_soil_groups.py --config soils_config.json
# =============================================================================

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from osgeo import gdal, ogr, osr

gdal.UseExceptions()

SDA_URL_DEFAULT = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
HSG_CODE = {"A": 1, "B": 2, "C": 3, "D": 4}
M_PER_DEG_LAT = 111320.0


def positive_float(value: str) -> float:
    x = float(value)
    if x <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return x


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cfg_get(config: dict[str, Any], key: str, default: Any) -> Any:
    return config.get(key, default)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Retrieve SSURGO hydrologic soil groups for site buffers."
    )
    p.add_argument("--config", help="Optional JSON config file. CLI options override config values.")
    p.add_argument("--root", help="Project root. Defaults to current directory or config root.")
    p.add_argument("--csv", dest="csv_rel", help="CSV path, absolute or relative to --root.")
    p.add_argument("--out-rel", help="Output root, absolute or relative to --root.")
    p.add_argument("--id-col", help="Site ID column in the CSV.")
    p.add_argument("--lat-col", help="Latitude column in the CSV.")
    p.add_argument("--lon-col", help="Longitude column in the CSV.")
    p.add_argument("--buffer-m", type=positive_float, help="Half-width of the SDA query box in meters.")
    p.add_argument("--pixel-deg", type=positive_float, help="Output raster pixel size in degrees.")
    p.add_argument("--pause-s", type=float, help="Pause between SDA calls in seconds.")
    p.add_argument("--timeout-s", type=float, help="SDA request timeout in seconds.")
    p.add_argument("--sda-url", help="SDA post.rest endpoint.")
    p.add_argument("--force", action="store_true", help="Overwrite existing per-site GPKG files.")
    p.add_argument("--limit", type=int, help="Process only the first N valid CSV rows; useful for testing.")
    return p


def parse_args() -> argparse.Namespace:
    cli = build_parser().parse_args()
    config = load_config(cli.config)

    def value(name: str, default: Any) -> Any:
        cli_value = getattr(cli, name)
        return cli_value if cli_value is not None else cfg_get(config, name, default)

    root = Path(value("root", ".")).expanduser().resolve()
    csv_rel = value("csv_rel", "WS3_GIS/ws3_site_locations.csv")
    out_rel = value("out_rel", "WS3_GIS")

    cli.root = root
    cli.csv_path = resolve_path(root, csv_rel)
    cli.out_root = resolve_path(root, out_rel)
    cli.id_col = value("id_col", "project_no")
    cli.lat_col = value("lat_col", "lat")
    cli.lon_col = value("lon_col", "lon")
    cli.buffer_m = float(value("buffer_m", 5000.0))
    cli.pixel_deg = float(value("pixel_deg", 0.0003))
    cli.pause_s = float(value("pause_s", 1.0))
    cli.timeout_s = float(value("timeout_s", 120.0))
    cli.sda_url = value("sda_url", SDA_URL_DEFAULT)
    cli.force = bool(cli.force or cfg_get(config, "force", False))
    cli.limit = value("limit", None)
    return cli


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def sanitize(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", text.strip())
    return text.rstrip(". ") or "unnamed"


def bbox_wkt(lat: float, lon: float, buffer_m: float) -> str:
    meters_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat)) or 1.0
    dlat = buffer_m / M_PER_DEG_LAT
    dlon = buffer_m / meters_per_deg_lon
    xmin, ymin, xmax, ymax = lon - dlon, lat - dlat, lon + dlon, lat + dlat
    return f"POLYGON(({xmin} {ymin},{xmax} {ymin},{xmax} {ymax},{xmin} {ymax},{xmin} {ymin}))"


def sda_post(sql: str, url: str, timeout_s: float) -> dict[str, Any]:
    payload = json.dumps({"format": "JSON+COLUMNNAME", "query": sql}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sda_query(wkt: str, url: str, timeout_s: float) -> dict[str, Any]:
    sql = (
        "SELECT mu.mukey, mag.hydgrpdcd, mu.mupolygongeo.STAsText() AS geom_wkt "
        "FROM mupolygon AS mu INNER JOIN muaggatt AS mag ON mu.mukey = mag.mukey "
        "WHERE mu.mupolygongeo.STIntersects("
        f"geometry::STGeomFromText('{wkt}', 4326)) = 1"
    )
    return sda_post(sql, url, timeout_s)


def resolve_hsg(raw: Any) -> str:
    if raw is None:
        return ""
    value = str(raw).strip().upper()
    if not value:
        return ""
    return value.split("/")[-1].strip() if "/" in value else value


def require_columns(rows: list[dict[str, str]], required: list[str], csv_path: Path) -> None:
    if not rows:
        raise ValueError(f"CSV is empty: {csv_path}")
    missing = [col for col in required if col not in rows[0]]
    if missing:
        available = ", ".join(rows[0].keys())
        raise ValueError(f"Missing column(s) {missing} in {csv_path}. Available columns: {available}")


def read_sites(csv_path: Path, id_col: str, lat_col: str, lon_col: str) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    require_columns(rows, [id_col, lat_col, lon_col], csv_path)
    return rows


def convert(sda_json: dict[str, Any], out_dir: Path, pixel_deg: float) -> tuple[int, dict[str, int], int]:
    table = sda_json.get("Table")
    if not table or len(table) < 2:
        return 0, {}, 0

    header = table[0]
    i_mukey = header.index("mukey")
    i_hg = header.index("hydgrpdcd")
    i_wkt = header.index("geom_wkt")

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)

    gpkg = out_dir / "hydrologic_soil_groups.gpkg"
    if gpkg.exists():
        gpkg.unlink()

    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(gpkg))
    lyr = ds.CreateLayer("hsg", srs, ogr.wkbMultiPolygon)
    for name, field_type in [
        ("mukey", ogr.OFTString),
        ("hydgrpdcd", ogr.OFTString),
        ("hsg", ogr.OFTString),
        ("hsg_code", ogr.OFTInteger),
    ]:
        lyr.CreateField(ogr.FieldDefn(name, field_type))

    dual: dict[str, int] = {}
    null_count = 0
    n_features = 0

    for row in table[1:]:
        wkt = row[i_wkt]
        if not wkt:
            continue

        geom = ogr.CreateGeometryFromWkt(wkt)
        if geom is None:
            continue
        if geom.GetGeometryType() == ogr.wkbPolygon:
            multi = ogr.Geometry(ogr.wkbMultiPolygon)
            multi.AddGeometry(geom)
            geom = multi

        raw_hsg = row[i_hg]
        hsg = resolve_hsg(raw_hsg)
        code = HSG_CODE.get(hsg, 0)
        if raw_hsg and "/" in str(raw_hsg):
            dual[str(raw_hsg)] = dual.get(str(raw_hsg), 0) + 1
        if code == 0:
            null_count += 1

        feat = ogr.Feature(lyr.GetLayerDefn())
        feat.SetField("mukey", str(row[i_mukey]))
        feat.SetField("hydgrpdcd", "" if raw_hsg is None else str(raw_hsg))
        feat.SetField("hsg", hsg)
        feat.SetField("hsg_code", code)
        feat.SetGeometry(geom)
        lyr.CreateFeature(feat)
        feat = None
        n_features += 1

    ds = None

    if n_features:
        ds = ogr.Open(str(gpkg))
        xmin, xmax, ymin, ymax = ds.GetLayer(0).GetExtent()
        ds = None
        nx = max(1, int(round((xmax - xmin) / pixel_deg)))
        ny = max(1, int(round((ymax - ymin) / pixel_deg)))
        gdal.Rasterize(
            str(out_dir / "hsg.tif"),
            str(gpkg),
            options=gdal.RasterizeOptions(
                attribute="hsg_code",
                outputType=gdal.GDT_Byte,
                width=nx,
                height=ny,
                outputBounds=[xmin, ymin, xmax, ymax],
                outputSRS="EPSG:4326",
                noData=255,
                creationOptions=["COMPRESS=LZW"],
            ),
        )

    return n_features, dual, null_count


def process_sites(args: argparse.Namespace) -> int:
    rows = read_sites(args.csv_path, args.id_col, args.lat_col, args.lon_col)
    if args.limit:
        rows = rows[: args.limit]

    args.out_root.mkdir(parents=True, exist_ok=True)
    print(f"CSV: {args.csv_path}")
    print(f"Output root: {args.out_root}")
    print(f"Sites: {len(rows)}  buffer: {args.buffer_m:.0f} m  pixel: {args.pixel_deg:g} deg\n")

    ok = skipped = failed = 0
    for row in rows:
        site_raw = (row.get(args.id_col) or "").strip()
        try:
            lat = float(row[args.lat_col])
            lon = float(row[args.lon_col])
        except (KeyError, ValueError, TypeError):
            print(f"  {site_raw or '?':<16s} bad coordinate, skip")
            failed += 1
            continue

        site_id = sanitize(site_raw)
        out_dir = args.out_root / site_id / "soils"
        out_dir.mkdir(parents=True, exist_ok=True)
        gpkg = out_dir / "hydrologic_soil_groups.gpkg"
        if gpkg.exists() and gpkg.stat().st_size > 0 and not args.force:
            print(f"  {site_id:<16s} exists, skip")
            skipped += 1
            continue

        try:
            response = sda_query(bbox_wkt(lat, lon, args.buffer_m), args.sda_url, args.timeout_s)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  {site_id:<16s} SDA FAILED: {exc}")
            failed += 1
            continue

        with open(out_dir / f"{site_id}_soils.json", "w", encoding="utf-8") as f:
            json.dump(response, f, indent=2)

        n_features, dual, null_count = convert(response, out_dir, args.pixel_deg)
        if n_features == 0:
            print(f"  {site_id:<16s} NO SOILS returned (SSURGO gap? check STATSGO)")
            failed += 1
        else:
            extra = []
            if dual:
                extra.append("dual->/D: " + ",".join(f"{k} x{v}" for k, v in sorted(dual.items())))
            if null_count:
                extra.append(f"NULL: {null_count}")
            print(f"  {site_id:<16s} ok  {n_features} polys  {'; '.join(extra)}")
            ok += 1

        time.sleep(max(0.0, args.pause_s))

    print(f"\nDone. {ok} ok, {skipped} skipped, {failed} failed.")
    return 0 if failed == 0 else 1


def main() -> int:
    try:
        return process_sites(parse_args())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

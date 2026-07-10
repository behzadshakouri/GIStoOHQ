#!/usr/bin/env python3
# =============================================================================
# retrieve_soil_texture.py
#
# Query USDA Soil Data Access (SDA) for SSURGO map unit polygons around each
# site, attach dominant-component top-soil texture, and write per-site GIS
# deliverables.
#
# Outputs per site:
#   <out-root>/<site>/soils/<site>_texture.json
#   <out-root>/<site>/soils/soil_texture.gpkg
#   <out-root>/<site>/soils/texture_code.tif
#   <out-root>/<site>/soils/{sand,silt,clay}_pct.tif
#
# Example:
#   python3 retrieve_soil_texture.py \
#       --root /path/to/project \
#       --csv WS3_GIS/ws3_site_locations.csv \
#       --out-rel WS3_GIS \
#       --id-col project_no --lat-col lat --lon-col lon
#
# A JSON config can also be used:
#   python3 retrieve_soil_texture.py --config soils_config.json
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
M_PER_DEG_LAT = 111320.0
NODATA_PCT = -9999.0
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
    p = argparse.ArgumentParser(description="Retrieve SSURGO top-soil texture for site buffers.")
    p.add_argument("--config", help="Optional JSON config file. CLI options override config values.")
    p.add_argument("--root", help="Project root. Defaults to current directory or config root.")
    p.add_argument("--csv", dest="csv_rel", help="CSV path, absolute or relative to --root.")
    p.add_argument("--out-rel", help="Output root, absolute or relative to --root.")
    p.add_argument("--id-col", help="Site ID column in the CSV.")
    p.add_argument("--lat-col", help="Latitude column in the CSV.")
    p.add_argument("--lon-col", help="Longitude column in the CSV.")
    p.add_argument("--buffer-m", type=positive_float, help="Half-width of the SDA query box in meters.")
    p.add_argument("--pixel-deg", type=positive_float, help="Output raster pixel size in degrees.")
    p.add_argument("--top-depth-cm", type=positive_float, help="Top-soil averaging depth in cm.")
    p.add_argument("--mukey-chunk", type=int, help="Number of mukeys per SDA horizon query.")
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
    cli.top_depth_cm = float(value("top_depth_cm", 30.0))
    cli.mukey_chunk = int(value("mukey_chunk", 500))
    cli.pause_s = float(value("pause_s", 1.0))
    cli.timeout_s = float(value("timeout_s", 180.0))
    cli.sda_url = value("sda_url", SDA_URL_DEFAULT)
    cli.force = bool(cli.force or cfg_get(config, "force", False))
    cli.limit = value("limit", None)
    if cli.mukey_chunk <= 0:
        raise ValueError("mukey_chunk must be positive")
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


def sda_polygons(wkt: str, url: str, timeout_s: float) -> dict[str, Any]:
    sql = (
        "SELECT mu.mukey, mu.mupolygongeo.STAsText() AS geom_wkt "
        "FROM mupolygon AS mu "
        "WHERE mu.mupolygongeo.STIntersects("
        f"geometry::STGeomFromText('{wkt}', 4326)) = 1"
    )
    return sda_post(sql, url, timeout_s)


def sda_horizons(mukeys: list[str], top_depth_cm: float, url: str, timeout_s: float) -> dict[str, Any]:
    quoted = ",".join(f"'{m}'" for m in mukeys)
    sql = (
        "SELECT c.mukey, c.cokey, c.comppct_r, c.compname, "
        "ch.hzdept_r, ch.hzdepb_r, ch.sandtotal_r, ch.silttotal_r, "
        "ch.claytotal_r, ch.om_r "
        "FROM component AS c "
        "INNER JOIN chorizon AS ch ON ch.cokey = c.cokey "
        f"WHERE c.mukey IN ({quoted}) "
        f"AND ch.hzdept_r < {top_depth_cm}"
    )
    return sda_post(sql, url, timeout_s)


def usda_texture(sand: float, silt: float, clay: float) -> str:
    """USDA soil texture triangle. Rule order matters; first match wins."""
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


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def topsoil_by_mukey(sda_json: dict[str, Any], top_depth_cm: float) -> dict[str, dict[str, Any]]:
    """Collapse component/horizon table to one top-soil record per mukey."""
    table = sda_json.get("Table")
    if not table or len(table) < 2:
        return {}

    header = table[0]
    cols = (
        "mukey",
        "cokey",
        "comppct_r",
        "compname",
        "hzdept_r",
        "hzdepb_r",
        "sandtotal_r",
        "silttotal_r",
        "claytotal_r",
        "om_r",
    )
    ix = {col: header.index(col) for col in cols}

    components: dict[tuple[str, str], dict[str, Any]] = {}
    for row in table[1:]:
        mukey = str(row[ix["mukey"]])
        cokey = str(row[ix["cokey"]])
        comp = components.setdefault(
            (mukey, cokey),
            {
                "pct": as_float(row[ix["comppct_r"]]) or 0.0,
                "name": row[ix["compname"]] or "",
                "horizons": [],
            },
        )
        top = as_float(row[ix["hzdept_r"]])
        bottom = as_float(row[ix["hzdepb_r"]])
        if top is None or bottom is None or bottom <= top:
            continue
        comp["horizons"].append(
            (
                top,
                bottom,
                as_float(row[ix["sandtotal_r"]]),
                as_float(row[ix["silttotal_r"]]),
                as_float(row[ix["claytotal_r"]]),
                as_float(row[ix["om_r"]]),
            )
        )

    dominant: dict[str, tuple[str, dict[str, Any]]] = {}
    for (mukey, cokey), comp in components.items():
        current = dominant.get(mukey)
        if current is None:
            dominant[mukey] = (cokey, comp)
            continue
        current_cokey, current_comp = current
        if comp["pct"] > current_comp["pct"] or (
            comp["pct"] == current_comp["pct"] and cokey < current_cokey
        ):
            dominant[mukey] = (cokey, comp)

    out: dict[str, dict[str, Any]] = {}
    for mukey, (_cokey, comp) in dominant.items():
        acc = [0.0, 0.0, 0.0, 0.0]
        wt = [0.0, 0.0, 0.0, 0.0]
        for top, bottom, sand, silt, clay, om in comp["horizons"]:
            thickness = min(bottom, top_depth_cm) - top
            if thickness <= 0:
                continue
            for i, value in enumerate((sand, silt, clay, om)):
                if value is not None:
                    acc[i] += thickness * value
                    wt[i] += thickness

        values = [acc[i] / wt[i] if wt[i] > 0 else None for i in range(4)]
        sand, silt, clay, om = values
        if None in (sand, silt, clay):
            texture = ""
            code = 0
        else:
            total = sand + silt + clay
            if total > 0:
                sand, silt, clay = (100.0 * sand / total, 100.0 * silt / total, 100.0 * clay / total)
            texture = usda_texture(sand, silt, clay)
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


def convert(
    polygon_json: dict[str, Any],
    texture_by_mukey: dict[str, dict[str, Any]],
    out_dir: Path,
    pixel_deg: float,
) -> tuple[int, int, dict[str, int]]:
    table = polygon_json.get("Table")
    if not table or len(table) < 2:
        return 0, 0, {}

    header = table[0]
    i_mukey = header.index("mukey")
    i_wkt = header.index("geom_wkt")

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)

    gpkg = out_dir / "soil_texture.gpkg"
    if gpkg.exists():
        gpkg.unlink()

    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(gpkg))
    lyr = ds.CreateLayer("texture", srs, ogr.wkbMultiPolygon)
    for name, field_type in [
        ("mukey", ogr.OFTString),
        ("compname", ogr.OFTString),
        ("comppct", ogr.OFTReal),
        ("sand", ogr.OFTReal),
        ("silt", ogr.OFTReal),
        ("clay", ogr.OFTReal),
        ("om", ogr.OFTReal),
        ("texture", ogr.OFTString),
        ("texture_code", ogr.OFTInteger),
    ]:
        lyr.CreateField(ogr.FieldDefn(name, field_type))

    counts: dict[str, int] = {}
    unrated = 0
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

        mukey = str(row[i_mukey])
        texture = texture_by_mukey.get(mukey)
        feat = ogr.Feature(lyr.GetLayerDefn())
        feat.SetField("mukey", mukey)

        if texture is None or texture["texture_code"] == 0:
            feat.SetField("compname", "" if texture is None else texture["compname"])
            feat.SetField("texture", "")
            feat.SetField("texture_code", 0)
            for name in ("sand", "silt", "clay", "om"):
                feat.SetField(name, NODATA_PCT)
            unrated += 1
        else:
            feat.SetField("compname", texture["compname"])
            feat.SetField("comppct", texture["comppct"])
            for name in ("sand", "silt", "clay"):
                feat.SetField(name, texture[name])
            feat.SetField("om", NODATA_PCT if texture["om"] is None else texture["om"])
            feat.SetField("texture", texture["texture"])
            feat.SetField("texture_code", texture["texture_code"])
            counts[texture["texture"]] = counts.get(texture["texture"], 0) + 1

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
        common = dict(
            width=nx,
            height=ny,
            outputBounds=[xmin, ymin, xmax, ymax],
            outputSRS="EPSG:4326",
            creationOptions=["COMPRESS=LZW"],
        )
        gdal.Rasterize(
            str(out_dir / "texture_code.tif"),
            str(gpkg),
            options=gdal.RasterizeOptions(
                attribute="texture_code", outputType=gdal.GDT_Byte, noData=255, **common
            ),
        )
        for name in ("sand", "silt", "clay"):
            gdal.Rasterize(
                str(out_dir / f"{name}_pct.tif"),
                str(gpkg),
                options=gdal.RasterizeOptions(
                    attribute=name, outputType=gdal.GDT_Float32, noData=NODATA_PCT, **common
                ),
            )

    return n_features, unrated, counts


def process_sites(args: argparse.Namespace) -> int:
    rows = read_sites(args.csv_path, args.id_col, args.lat_col, args.lon_col)
    if args.limit:
        rows = rows[: args.limit]

    args.out_root.mkdir(parents=True, exist_ok=True)
    print(f"CSV: {args.csv_path}")
    print(f"Output root: {args.out_root}")
    print(
        f"Sites: {len(rows)}  buffer: {args.buffer_m:.0f} m  "
        f"top soil: 0-{args.top_depth_cm:.0f} cm  pixel: {args.pixel_deg:g} deg\n"
    )

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
        gpkg = out_dir / "soil_texture.gpkg"
        if gpkg.exists() and gpkg.stat().st_size > 0 and not args.force:
            print(f"  {site_id:<16s} exists, skip")
            skipped += 1
            continue

        try:
            polygons = sda_polygons(bbox_wkt(lat, lon, args.buffer_m), args.sda_url, args.timeout_s)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  {site_id:<16s} SDA FAILED (polygons): {exc}")
            failed += 1
            continue

        polygon_table = polygons.get("Table")
        if not polygon_table or len(polygon_table) < 2:
            print(f"  {site_id:<16s} NO SOILS returned (SSURGO gap? check STATSGO)")
            failed += 1
            time.sleep(max(0.0, args.pause_s))
            continue

        i_mukey = polygon_table[0].index("mukey")
        mukeys = sorted({str(row[i_mukey]) for row in polygon_table[1:]})
        texture_by_mukey: dict[str, dict[str, Any]] = {}
        horizon_raw: list[dict[str, Any]] = []

        try:
            for i in range(0, len(mukeys), args.mukey_chunk):
                time.sleep(max(0.0, args.pause_s))
                response = sda_horizons(
                    mukeys[i : i + args.mukey_chunk],
                    args.top_depth_cm,
                    args.sda_url,
                    args.timeout_s,
                )
                horizon_raw.append(response)
                texture_by_mukey.update(topsoil_by_mukey(response, args.top_depth_cm))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  {site_id:<16s} SDA FAILED (horizons): {exc}")
            failed += 1
            continue

        with open(out_dir / f"{site_id}_texture.json", "w", encoding="utf-8") as f:
            json.dump({"polygons": polygons, "horizons": horizon_raw}, f, indent=2)

        n_features, unrated, counts = convert(polygons, texture_by_mukey, out_dir, args.pixel_deg)
        if n_features == 0:
            print(f"  {site_id:<16s} NO usable geometry")
            failed += 1
        else:
            top = sorted(counts.items(), key=lambda item: -item[1])[:3]
            extra = [f"{name} x{count}" for name, count in top]
            if unrated:
                extra.append(f"unrated: {unrated}")
            print(f"  {site_id:<16s} ok  {n_features} polys  {len(mukeys)} mukeys  {'; '.join(extra)}")
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

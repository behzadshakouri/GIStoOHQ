# =============================================================================
# retrieve_soil_texture.py
#
# For every WS3 site in the CSV: query USDA Soil Data Access (SDA) for the
# SSURGO map unit polygons covering a buffered bbox around the site, attach
# top-soil texture to each polygon, then write per-site deliverables:
#   WS3_GIS/<site>/soils/<site>_texture.json        (raw SDA responses, archived)
#   WS3_GIS/<site>/soils/soil_texture.gpkg          (polygons: mukey,compname,
#                                                    sand,silt,clay,om,texture,texture_code)
#   WS3_GIS/<site>/soils/texture_code.tif           (rasterized class, EPSG:4326)
#   WS3_GIS/<site>/soils/{sand,silt,clay}_pct.tif   (rasterized fractions)
#
# Source: SDA post.rest, two calls per site -- (1) mupolygon spatial query for
# geometry + mukey, (2) component JOIN chorizon tabular query for the mukeys.
#
# "Top soil" = thickness-weighted mean of sand/silt/clay over the horizons that
# overlap 0-TOP_DEPTH_CM of the DOMINANT component (highest comppct_r, ties
# broken by lowest cokey). Texture class is recomputed from those means with
# the USDA texture triangle rather than taken from chtexture, so every polygon
# is classified the same way. Map units whose dominant component has no horizon
# data (rock outcrop, water, urban land) get texture_code 0 and are counted per
# site -- they must be noted in the sealed doc.
#
# CRS: EPSG:4326 (native SDA). Skips sites whose gpkg already exists.
#
# Usage: python3 retrieve_soil_texture.py
# =============================================================================
import csv, json, math, os, sys, time, urllib.request, urllib.error
from osgeo import ogr, gdal, osr
gdal.UseExceptions()

# --- settings --------------------------------------------------------------
ROOT     = "/home/arash/Dropbox/Chloeta/NHA"
OUT_REL  = "WS3_GIS"
BUFFER_M = 5000.0                      # bbox half-width (m), matches land cover
PIXEL_DEG = 0.0003                     # ~33 m raster cells
TOP_DEPTH_CM = 30.0                    # depth window averaged as "top soil"

CSV_REL  = "WS3_GIS/ws3_site_locations.csv"
ID_COL   = "project_no"
LAT_COL  = "lat"
LON_COL  = "lon"

SDA_URL  = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
MUKEY_CHUNK = 500                      # mukeys per tabular query
PAUSE_S  = 1.0                         # be polite between SDA calls

TEXTURE_CODE = {
    "sand": 1, "loamy sand": 2, "sandy loam": 3, "loam": 4,
    "silt loam": 5, "silt": 6, "sandy clay loam": 7, "clay loam": 8,
    "silty clay loam": 9, "sandy clay": 10, "silty clay": 11, "clay": 12,
}
NODATA_PCT = -9999.0
# ---------------------------------------------------------------------------

_M_PER_DEG_LAT = 111320.0

def sanitize(s):
    import re
    s = re.sub(r'[\\/:*?"<>|]', "_", s.strip())
    return s.rstrip(". ") or "unnamed"

def bbox_wkt(lat, lon, buf):
    mlon = _M_PER_DEG_LAT * math.cos(math.radians(lat)) or 1.0
    dlat, dlon = buf/_M_PER_DEG_LAT, buf/mlon
    a, b, c, d = lon-dlon, lat-dlat, lon+dlon, lat+dlat
    return f"POLYGON(({a} {b},{c} {b},{c} {d},{a} {d},{a} {b}))"

def sda_post(sql):
    payload = json.dumps({"format": "JSON+COLUMNNAME", "query": sql}).encode()
    req = urllib.request.Request(SDA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())

def sda_polygons(wkt):
    return sda_post(
        "SELECT mu.mukey, mu.mupolygongeo.STAsText() AS geom_wkt "
        "FROM mupolygon AS mu "
        "WHERE mu.mupolygongeo.STIntersects("
        "geometry::STGeomFromText('" + wkt + "', 4326)) = 1")

def sda_horizons(mukeys):
    keys = ",".join("'%s'" % k for k in mukeys)
    return sda_post(
        "SELECT c.mukey, c.cokey, c.comppct_r, c.compname, "
        "ch.hzdept_r, ch.hzdepb_r, ch.sandtotal_r, ch.silttotal_r, "
        "ch.claytotal_r, ch.om_r "
        "FROM component AS c "
        "INNER JOIN chorizon AS ch ON ch.cokey = c.cokey "
        "WHERE c.mukey IN (" + keys + ") "
        "AND ch.hzdept_r < " + str(TOP_DEPTH_CM))

def usda_texture(sand, silt, clay):
    """USDA soil texture triangle. sand/silt/clay are percentages of the
    <2 mm fraction. Rule order matters -- the first match wins."""
    if silt + 1.5*clay < 15:                                        return "sand"
    if silt + 1.5*clay >= 15 and silt + 2*clay < 30:                return "loamy sand"
    if (7 <= clay < 20 and sand > 52 and silt + 2*clay >= 30) or \
       (clay < 7 and silt < 50 and silt + 2*clay >= 30):            return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:           return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
                                                                    return "silt loam"
    if silt >= 80 and clay < 12:                                    return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:                 return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:                         return "clay loam"
    if 27 <= clay < 40 and sand <= 20:                              return "silty clay loam"
    if clay >= 35 and sand > 45:                                    return "sandy clay"
    if clay >= 40 and silt >= 40:                                   return "silty clay"
    if clay >= 40 and sand <= 45 and silt < 40:                     return "clay"
    return ""

def _num(v):
    if v is None or v == "": return None
    try: return float(v)
    except (TypeError, ValueError): return None

def topsoil_by_mukey(sda_json):
    """Collapse the component/horizon table to one top-soil record per mukey."""
    tbl = sda_json.get("Table")
    if not tbl or len(tbl) < 2: return {}
    hdr = tbl[0]
    ix = {c: hdr.index(c) for c in
          ("mukey", "cokey", "comppct_r", "compname",
           "hzdept_r", "hzdepb_r", "sandtotal_r", "silttotal_r",
           "claytotal_r", "om_r")}

    # group horizons by (mukey, cokey), remembering the component header
    comps = {}
    for row in tbl[1:]:
        mukey, cokey = str(row[ix["mukey"]]), str(row[ix["cokey"]])
        c = comps.setdefault((mukey, cokey), {
            "pct": _num(row[ix["comppct_r"]]) or 0.0,
            "name": row[ix["compname"]] or "",
            "hz": []})
        top, bot = _num(row[ix["hzdept_r"]]), _num(row[ix["hzdepb_r"]])
        if top is None or bot is None or bot <= top: continue
        c["hz"].append((top, bot,
                        _num(row[ix["sandtotal_r"]]),
                        _num(row[ix["silttotal_r"]]),
                        _num(row[ix["claytotal_r"]]),
                        _num(row[ix["om_r"]])))

    # dominant component per mukey: highest comppct_r, ties -> lowest cokey
    best = {}
    for (mukey, cokey), c in comps.items():
        cur = best.get(mukey)
        if cur is None or (c["pct"], cur[0]) > (cur[1]["pct"], cokey):
            best[mukey] = (cokey, c)   # higher pct wins; equal pct -> lower cokey

    out = {}
    for mukey, (cokey, c) in best.items():
        acc = [0.0, 0.0, 0.0, 0.0]      # sand, silt, clay, om -- thickness*value
        wt = [0.0, 0.0, 0.0, 0.0]       # thickness with a non-null value
        for top, bot, sa, si, cl, om in c["hz"]:
            th = min(bot, TOP_DEPTH_CM) - top
            if th <= 0: continue
            for i, v in enumerate((sa, si, cl, om)):
                if v is not None:
                    acc[i] += th * v; wt[i] += th
        vals = [acc[i]/wt[i] if wt[i] > 0 else None for i in range(4)]
        sand, silt, clay, om = vals
        if None in (sand, silt, clay):
            tex, code = "", 0
        else:
            # SSURGO fractions can drift a percent or two off 100; renormalize
            tot = sand + silt + clay
            if tot > 0:
                sand, silt, clay = (100.0*sand/tot, 100.0*silt/tot, 100.0*clay/tot)
            tex = usda_texture(sand, silt, clay)
            code = TEXTURE_CODE.get(tex, 0)
        out[mukey] = {"compname": c["name"], "comppct": c["pct"],
                      "sand": sand, "silt": silt, "clay": clay, "om": om,
                      "texture": tex, "texture_code": code}
    return out

def convert(poly_json, tex, out_dir, pixel_deg):
    tbl = poly_json.get("Table")
    if not tbl or len(tbl) < 2:
        return (0, 0, {})            # no soils returned
    hdr = tbl[0]
    i_mukey, i_wkt = hdr.index("mukey"), hdr.index("geom_wkt")
    srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
    gpkg = os.path.join(out_dir, "soil_texture.gpkg")
    if os.path.exists(gpkg): os.remove(gpkg)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(gpkg)
    lyr = ds.CreateLayer("texture", srs, ogr.wkbMultiPolygon)
    for nm, ty in [("mukey", ogr.OFTString), ("compname", ogr.OFTString),
                   ("comppct", ogr.OFTReal), ("sand", ogr.OFTReal),
                   ("silt", ogr.OFTReal), ("clay", ogr.OFTReal),
                   ("om", ogr.OFTReal), ("texture", ogr.OFTString),
                   ("texture_code", ogr.OFTInteger)]:
        lyr.CreateField(ogr.FieldDefn(nm, ty))

    counts = {}; norated = 0; n = 0
    for row in tbl[1:]:
        wkt = row[i_wkt]
        if not wkt: continue
        geom = ogr.CreateGeometryFromWkt(wkt)
        if geom is None: continue
        if geom.GetGeometryType() == ogr.wkbPolygon:
            mg = ogr.Geometry(ogr.wkbMultiPolygon); mg.AddGeometry(geom); geom = mg
        mukey = str(row[i_mukey])
        t = tex.get(mukey)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("mukey", mukey)
        if t is None or t["texture_code"] == 0:
            f.SetField("compname", "" if t is None else t["compname"])
            f.SetField("texture", ""); f.SetField("texture_code", 0)
            for nm in ("sand", "silt", "clay", "om"): f.SetField(nm, NODATA_PCT)
            norated += 1
        else:
            f.SetField("compname", t["compname"]); f.SetField("comppct", t["comppct"])
            for nm in ("sand", "silt", "clay"): f.SetField(nm, t[nm])
            f.SetField("om", NODATA_PCT if t["om"] is None else t["om"])
            f.SetField("texture", t["texture"]); f.SetField("texture_code", t["texture_code"])
            counts[t["texture"]] = counts.get(t["texture"], 0) + 1
        f.SetGeometry(geom); lyr.CreateFeature(f); f = None; n += 1
    ds = None

    if n:
        ds = ogr.Open(gpkg); x0, x1, y0, y1 = ds.GetLayer(0).GetExtent(); ds = None
        nx = max(1, int(round((x1-x0)/pixel_deg))); ny = max(1, int(round((y1-y0)/pixel_deg)))
        common = dict(width=nx, height=ny, outputBounds=[x0, y0, x1, y1],
                      outputSRS="EPSG:4326", creationOptions=["COMPRESS=LZW"])
        gdal.Rasterize(os.path.join(out_dir, "texture_code.tif"), gpkg,
            options=gdal.RasterizeOptions(attribute="texture_code",
                outputType=gdal.GDT_Byte, noData=255, **common))
        for nm in ("sand", "silt", "clay"):
            gdal.Rasterize(os.path.join(out_dir, nm + "_pct.tif"), gpkg,
                options=gdal.RasterizeOptions(attribute=nm,
                    outputType=gdal.GDT_Float32, noData=NODATA_PCT, **common))
    return (n, norated, counts)

def main():
    csv_path = os.path.join(ROOT, CSV_REL)
    out_root = os.path.join(ROOT, OUT_REL)
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    print("Sites: %d  buffer: %.0f m  top soil: 0-%.0f cm  -> %s\n"
          % (len(rows), BUFFER_M, TOP_DEPTH_CM, out_root))
    ok = skip = fail = 0
    for r in rows:
        sid_raw = (r.get(ID_COL) or "").strip()
        try:
            lat = float(r[LAT_COL]); lon = float(r[LON_COL])
        except (KeyError, ValueError, TypeError):
            print("  %-16s bad coordinate, skip" % (sid_raw or "?")); fail += 1; continue
        sid = sanitize(sid_raw)
        out_dir = os.path.join(out_root, sid, "soils")
        os.makedirs(out_dir, exist_ok=True)
        gpkg = os.path.join(out_dir, "soil_texture.gpkg")
        if os.path.exists(gpkg) and os.path.getsize(gpkg) > 0:
            print("  %-16s exists, skip" % sid); skip += 1; continue

        try:
            polys = sda_polygons(bbox_wkt(lat, lon, BUFFER_M))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print("  %-16s SDA FAILED (polygons): %s" % (sid, e)); fail += 1; continue
        ptbl = polys.get("Table")
        if not ptbl or len(ptbl) < 2:
            print("  %-16s NO SOILS returned (SSURGO gap? check STATSGO)" % sid)
            fail += 1; time.sleep(PAUSE_S); continue

        i_mukey = ptbl[0].index("mukey")
        mukeys = sorted({str(row[i_mukey]) for row in ptbl[1:]})
        tex, hz_raw = {}, []
        try:
            for i in range(0, len(mukeys), MUKEY_CHUNK):
                time.sleep(PAUSE_S)
                resp = sda_horizons(mukeys[i:i+MUKEY_CHUNK])
                hz_raw.append(resp)
                tex.update(topsoil_by_mukey(resp))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print("  %-16s SDA FAILED (horizons): %s" % (sid, e)); fail += 1; continue

        with open(os.path.join(out_dir, sid + "_texture.json"), "w") as f:
            json.dump({"polygons": polys, "horizons": hz_raw}, f)
        n, norated, counts = convert(polys, tex, out_dir, PIXEL_DEG)
        if n == 0:
            print("  %-16s NO usable geometry" % sid); fail += 1
        else:
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
            extra = ["%s x%d" % kv for kv in top]
            if norated: extra.append("unrated: %d" % norated)
            print("  %-16s ok  %d polys  %d mukeys  %s"
                  % (sid, n, len(mukeys), "; ".join(extra)))
            ok += 1
        time.sleep(PAUSE_S)
    print("\nDone. %d ok, %d skipped, %d failed." % (ok, skip, fail))

if __name__ == "__main__":
    main()

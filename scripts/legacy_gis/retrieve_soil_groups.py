# =============================================================================
# batch_soils.py
#
# For every WS3 site in the CSV: query USDA Soil Data Access (SDA) for the
# hydrologic soil group polygons covering a buffered bbox around the site,
# then write per-site deliverables:
#   WS3_GIS/<site>/soils/<site>_soils.json          (raw SDA response, archived)
#   WS3_GIS/<site>/soils/hydrologic_soil_groups.gpkg (polygons: mukey,hydgrpdcd,hsg,hsg_code)
#   WS3_GIS/<site>/soils/hsg.tif                      (rasterized hsg_code, EPSG:4326)
#
# Source: SDA post.rest (SSURGO mupolygon JOIN muaggatt on mukey). Dual HSG
# classes (A/D,B/D,C/D) resolve to the UNDRAINED member (the "/D" side), the
# conservative runoff choice for arid terrain. NULL/unrated units are kept
# (hsg_code 0). Both are reported per site and must be noted in the sealed doc.
#
# CRS: EPSG:4326 (native SDA). Skips sites whose gpkg already exists.
#
# Usage: python3 batch_soils.py
# =============================================================================
import csv, json, math, os, sys, time, urllib.request, urllib.error
from osgeo import ogr, gdal, osr
gdal.UseExceptions()

# --- settings --------------------------------------------------------------
ROOT     = "/home/arash/Dropbox/Chloeta/NHA"
CSV_REL  = "WS3_Site_Coordinates.csv"
OUT_REL  = "WS3_GIS"
BUFFER_M = 5000.0                      # bbox half-width (m), matches land cover
PIXEL_DEG = 0.0003                     # ~33 m raster cells

CSV_REL  = "WS3_GIS/ws3_site_locations.csv"   # new file
ID_COL   = "project_no"                        # was "Project No."
LAT_COL  = "lat"                               # was "Centroid Lat"
LON_COL  = "lon"                               # was "Centroid Lon"

SDA_URL  = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
HSG_CODE = {"A": 1, "B": 2, "C": 3, "D": 4}
PAUSE_S  = 1.0                         # be polite between SDA calls
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

def sda_query(wkt):
    sql = ("SELECT mu.mukey, mag.hydgrpdcd, mu.mupolygongeo.STAsText() AS geom_wkt "
           "FROM mupolygon AS mu INNER JOIN muaggatt AS mag ON mu.mukey = mag.mukey "
           "WHERE mu.mupolygongeo.STIntersects("
           "geometry::STGeomFromText('" + wkt + "', 4326)) = 1")
    payload = json.dumps({"format": "JSON+COLUMNNAME", "query": sql}).encode()
    req = urllib.request.Request(SDA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())

def resolve_hsg(raw):
    if raw is None: return ""
    v = raw.strip()
    if not v: return ""
    if "/" in v: return v.split("/")[-1].strip().upper()
    return v.upper()

def convert(sda_json, out_dir, pixel_deg):
    tbl = sda_json.get("Table")
    if not tbl or len(tbl) < 2:
        return (0, {}, 0)        # no soils returned
    hdr = tbl[0]
    i_mukey, i_hg, i_wkt = hdr.index("mukey"), hdr.index("hydgrpdcd"), hdr.index("geom_wkt")
    srs = osr.SpatialReference(); srs.ImportFromEPSG(4326)
    gpkg = os.path.join(out_dir, "hydrologic_soil_groups.gpkg")
    if os.path.exists(gpkg): os.remove(gpkg)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(gpkg)
    lyr = ds.CreateLayer("hsg", srs, ogr.wkbMultiPolygon)
    for nm, ty in [("mukey", ogr.OFTString), ("hydgrpdcd", ogr.OFTString),
                   ("hsg", ogr.OFTString), ("hsg_code", ogr.OFTInteger)]:
        lyr.CreateField(ogr.FieldDefn(nm, ty))
    dual = {}; nullc = 0; n = 0
    for row in tbl[1:]:
        wkt = row[i_wkt]
        if not wkt: continue
        raw_hg = row[i_hg]
        hsg = resolve_hsg(raw_hg); code = HSG_CODE.get(hsg, 0)
        if raw_hg and "/" in str(raw_hg): dual[raw_hg] = dual.get(raw_hg, 0) + 1
        if code == 0: nullc += 1
        geom = ogr.CreateGeometryFromWkt(wkt)
        if geom is None: continue
        if geom.GetGeometryType() == ogr.wkbPolygon:
            mg = ogr.Geometry(ogr.wkbMultiPolygon); mg.AddGeometry(geom); geom = mg
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("mukey", str(row[i_mukey]))
        f.SetField("hydgrpdcd", "" if raw_hg is None else str(raw_hg))
        f.SetField("hsg", hsg); f.SetField("hsg_code", code)
        f.SetGeometry(geom); lyr.CreateFeature(f); f = None; n += 1
    ds = None
    if n:
        ds = ogr.Open(gpkg); x0, x1, y0, y1 = ds.GetLayer(0).GetExtent(); ds = None
        nx = max(1, int(round((x1-x0)/pixel_deg))); ny = max(1, int(round((y1-y0)/pixel_deg)))
        gdal.Rasterize(os.path.join(out_dir, "hsg.tif"), gpkg,
            options=gdal.RasterizeOptions(attribute="hsg_code", outputType=gdal.GDT_Byte,
                width=nx, height=ny, outputBounds=[x0, y0, x1, y1],
                outputSRS="EPSG:4326", noData=255, creationOptions=["COMPRESS=LZW"]))
    return (n, dual, nullc)

def main():
    csv_path = os.path.join(ROOT, CSV_REL)
    out_root = os.path.join(ROOT, OUT_REL)
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    print("Sites: %d  buffer: %.0f m  -> %s\n" % (len(rows), BUFFER_M, out_root))
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
        gpkg = os.path.join(out_dir, "hydrologic_soil_groups.gpkg")
        if os.path.exists(gpkg) and os.path.getsize(gpkg) > 0:
            print("  %-16s exists, skip" % sid); skip += 1; continue
        try:
            resp = sda_query(bbox_wkt(lat, lon, BUFFER_M))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print("  %-16s SDA FAILED: %s" % (sid, e)); fail += 1; continue
        json.dump(resp, open(os.path.join(out_dir, sid + "_soils.json"), "w"))
        n, dual, nullc = convert(resp, out_dir, PIXEL_DEG)
        if n == 0:
            print("  %-16s NO SOILS returned (SSURGO gap? check STATSGO)" % sid); fail += 1
        else:
            extra = []
            if dual:  extra.append("dual->/D: " + ",".join("%s x%d" % kv for kv in sorted(dual.items())))
            if nullc: extra.append("NULL: %d" % nullc)
            print("  %-16s ok  %d polys  %s" % (sid, n, "; ".join(extra)))
            ok += 1
        time.sleep(PAUSE_S)
    print("\nDone. %d ok, %d skipped, %d failed." % (ok, skip, fail))

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# =============================================================================
# download_agol_geojson.py
#
# Download one or more ArcGIS Feature Server layers to GeoJSON, paginating
# through all features (ArcGIS caps each response, typically 1000-2000), and
# handling token auth if the layers are not public. Each layer is written to
# its own GeoJSON, named by the layer's own name, in a shared output folder.
#
# Default target: NHA portal Dwelling Unit Inventory, layers 0, 5, 6, 7.
#
# USAGE (standalone or in the QGIS Python Console):
#   python3 download_agol_geojson.py
#
# If the layers need a token, either:
#   (a) set TOKEN = "..."  (paste a token you already have), or
#   (b) set USERNAME/PASSWORD and TOKEN_URL so the script calls generateToken.
# =============================================================================
import os
import re
import sys
import json
import time
import urllib.parse
import urllib.request

# --- settings --------------------------------------------------------------
SERVICE_URL = ("https://nhagisportal.com/arcgis/rest/services/Hosted/"
               "Dwelling_Unit_Inventory_Map/FeatureServer")
LAYER_IDS   = [0, 5, 6, 7]      # layers to download

OUT_DIR = os.path.expanduser(
    "~/Dropbox/Chloeta/NHA/WS3_GIS/dwelling_unit_inventory")

WHERE     = "1=1"               # all features; or e.g. "STATE='AZ'"
OUT_SR    = 4326                # 4326 = lon/lat WGS84; use 26912 for UTM 12N
PAGE_SIZE = 1000
TIMEOUT   = 60

# --- auth (leave blank if public) ------------------------------------------
TOKEN = os.environ.get("AGOL_TOKEN", "")
USERNAME  = ""
PASSWORD  = ""
TOKEN_URL = "https://nhagisportal.com/arcgis/tokens/generateToken"
REFERER   = "https://nhagisportal.com"
# ---------------------------------------------------------------------------


def http_get(url, params):
    q = urllib.parse.urlencode(params)
    full = url + ("&" if "?" in url else "?") + q
    req = urllib.request.Request(full, headers={"Referer": REFERER})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Referer": REFERER})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def get_token():
    """Return a token: use TOKEN if set, else call generateToken."""
    if TOKEN:
        return TOKEN
    if USERNAME and PASSWORD:
        print("Requesting token via generateToken ...")
        res = http_post(TOKEN_URL, {
            "username": USERNAME, "password": PASSWORD,
            "referer": REFERER, "f": "json", "expiration": 60,
        })
        if "token" not in res:
            raise Exception("generateToken failed: " + json.dumps(res))
        print("  token acquired.")
        return res["token"]
    return None


def maybe_auth(params, token):
    if token:
        params = dict(params)
        params["token"] = token
    return params


def safe_name(s, layer_id):
    """Filesystem-safe file stem from the layer name + id."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "layer")).strip("_")
    return "%02d_%s" % (layer_id, base)


def download_layer(layer_id, token):
    layer_url = "%s/%d" % (SERVICE_URL, layer_id)
    meta = http_get(layer_url, maybe_auth({"f": "json"}, token))
    if "error" in meta:
        raise Exception("Layer %d metadata error (token needed?): %s"
                        % (layer_id, json.dumps(meta["error"])))
    name = meta.get("name", "layer")
    srv_max = meta.get("maxRecordCount", PAGE_SIZE)
    page = min(PAGE_SIZE, srv_max) if srv_max else PAGE_SIZE
    print("\n[layer %d] %s | maxRecordCount=%s | page=%d"
          % (layer_id, name, srv_max, page))

    cnt = http_get(layer_url + "/query", maybe_auth({
        "where": WHERE, "returnCountOnly": "true", "f": "json"}, token))
    total = cnt.get("count")
    print("  total features:", total)

    features = []
    offset = 0
    crs_obj = None
    while True:
        gj = http_get(layer_url + "/query", maybe_auth({
            "where": WHERE, "outFields": "*", "outSR": OUT_SR,
            "f": "geojson", "resultOffset": offset,
            "resultRecordCount": page, "returnGeometry": "true"}, token))
        if isinstance(gj, dict) and gj.get("error"):
            raise Exception("Query error (layer %d, offset %d): %s"
                            % (layer_id, offset, json.dumps(gj["error"])))
        batch = gj.get("features", []) if isinstance(gj, dict) else []
        if crs_obj is None and isinstance(gj, dict) and "crs" in gj:
            crs_obj = gj["crs"]
        n = len(batch)
        features.extend(batch)
        print("    offset %6d  +%-4d  (total %d)" % (offset, n, len(features)))
        if n == 0:
            break
        if total is not None and len(features) >= total:
            break
        if n < page:
            break
        offset += n
        time.sleep(0.1)

    out = {"type": "FeatureCollection", "features": features}
    if crs_obj:
        out["crs"] = crs_obj
    out_path = os.path.join(OUT_DIR, safe_name(name, layer_id) + ".geojson")
    with open(out_path, "w") as fh:
        json.dump(out, fh)
    status = "OK" if (total is None or len(features) == total) else "MISMATCH"
    print("  wrote %d features -> %s  [%s]"
          % (len(features), os.path.basename(out_path), status))
    return (layer_id, name, len(features), total, out_path, status)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    token = get_token()
    print("Output folder:", OUT_DIR)
    print("Layers:", LAYER_IDS)

    results = []
    for lid in LAYER_IDS:
        try:
            results.append(download_layer(lid, token))
        except Exception as e:
            print("  !! layer %d FAILED: %s" % (lid, e))
            results.append((lid, "?", 0, None, "", "FAILED"))

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    for (lid, name, nfeat, total, path, status) in results:
        print("  layer %-2d  %-32s %6d / %-6s  %s"
              % (lid, (name or "")[:32], nfeat,
                 str(total), status))
    bad = [r for r in results if r[5] not in ("OK",)]
    if bad:
        print("\n  !! %d layer(s) need attention (see above)." % len(bad))


if __name__ == "__main__":
    main()

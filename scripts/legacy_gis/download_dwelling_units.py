# =============================================================================
# download_dwelling_units.py  (QGIS Python Console)
#
# Downloads selected layers of the NHA "Dwelling_Unit_Inventory_Map" hosted
# FeatureServer to GeoJSON files in a subfolder under WS3_GIS. Generates a
# portal token first (the query endpoint returns 499 "Token Required" without
# one), then paginates each layer against MaxRecordCount.
#
# Modeled on download_fbfa.py (which works against this portal). Key points that
# make auth succeed on this server:
#   - token from the PORTAL endpoint  /portal/sharing/rest/generateToken
#   - client=referer, and the SAME referer header sent on every query
#
# USAGE (QGIS Python Console):
#   exec(open("/home/arash/Dropbox/Chloeta/NHA/PythonScripts/download_dwelling_units.py").read())
#
# You'll be prompted for your portal password (masked, not stored). Username
# defaults to PORTAL_USER below -- change if needed. To preset the password,
# set  DU_PW = "..."  in the console before running.
# =============================================================================

import os
import json
import time
import getpass
import urllib.parse
import urllib.request

# --- settings --------------------------------------------------------------

ROOT    = "/home/arash/Dropbox/Chloeta/NHA"
OUT_DIR = os.path.join(ROOT, "WS3_GIS", "dwelling_unit_inventory")

PORTAL      = "https://nhagisportal.com/portal"
TOKEN_URL   = PORTAL + "/sharing/rest/generateToken"
PORTAL_USER = "Arashm"          # change if your login differs

SERVICE = ("https://nhagisportal.com/arcgis/rest/services/"
           "Hosted/Dwelling_Unit_Inventory_Map/FeatureServer")

# Layers to download. Names are auto-detected from each layer's metadata, so
# this is just the list of IDs you asked for.
LAYER_IDS = [0, 5, 6, 7]

OUT_SR    = 4326                # WGS84; reproject to UTM 12N (26912) before clipping
PAGE_SIZE = 2000
TIMEOUT   = 120

# ---------------------------------------------------------------------------

os.makedirs(OUT_DIR, exist_ok=True)


def post(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "QGIS-DU-dl"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def get_token(user, pw):
    params = {
        "username": user,
        "password": pw,
        "client": "referer",
        "referer": "https://nhagisportal.com",
        "expiration": 120,         # minutes
        "f": "json",
    }
    obj = json.loads(post(TOKEN_URL, params))
    if "token" not in obj:
        raise RuntimeError("Token request failed: %s" % obj)
    return obj["token"], params["referer"]


def get_json(url, params, referer):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={
        "User-Agent": "QGIS-DU-dl", "Referer": referer})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def layer_name(layer_id, token, referer):
    """Read the layer's own name from metadata (for the output filename)."""
    try:
        meta = get_json("%s/%d" % (SERVICE, layer_id),
                        {"f": "json", "token": token}, referer)
        nm = meta.get("name", "layer")
    except Exception:
        nm = "layer"
    import re
    nm = re.sub(r"[^A-Za-z0-9._-]+", "_", nm or "layer").strip("_")
    return "%02d_%s" % (layer_id, nm)


def query_layer(layer_id, token, referer):
    base = "%s/%d/query" % (SERVICE, layer_id)
    features = []
    offset = 0
    crs_block = None
    while True:
        params = {
            "where": "1=1", "outFields": "*", "outSR": OUT_SR,
            "f": "geojson", "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE, "returnGeometry": "true",
            "token": token,
        }
        gj = get_json(base, params, referer)
        if isinstance(gj, dict) and gj.get("error"):
            raise RuntimeError("Layer %d service error: %s"
                               % (layer_id, gj["error"]))
        page = gj.get("features", [])
        if crs_block is None and "crs" in gj:
            crs_block = gj["crs"]
        features.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.2)

    fc = {"type": "FeatureCollection", "features": features}
    if crs_block:
        fc["crs"] = crs_block
    return fc


# --- password entry (preset var -> QGIS dialog -> terminal) ----------------

def get_password():
    try:
        if DU_PW:                       # set DU_PW = "..." in the console to skip prompts
            return DU_PW
    except NameError:
        pass
    try:
        from qgis.PyQt.QtWidgets import QInputDialog, QLineEdit
        from qgis.utils import iface
        parent = iface.mainWindow() if iface else None
        pw, ok = QInputDialog.getText(
            parent, "Dwelling Unit download",
            "Portal password for %s:" % PORTAL_USER, QLineEdit.Password)
        if not ok or not pw:
            raise RuntimeError("Password entry cancelled.")
        return pw
    except ImportError:
        return getpass.getpass("Portal password for %s: " % PORTAL_USER)


# --- run -------------------------------------------------------------------

print("=" * 64)
print("Dwelling Unit Inventory download -> %s" % OUT_DIR)
print("=" * 64)

pw = get_password()
print("Requesting token...")
token, referer = get_token(PORTAL_USER, pw)
print("Token acquired (expires in ~120 min).")

summary = []
for lid in LAYER_IDS:
    name = layer_name(lid, token, referer)
    out_path = os.path.join(OUT_DIR, "%s.geojson" % name)
    print("\n[%2d] %-30s -> %s" % (lid, name, os.path.basename(out_path)))
    try:
        fc = query_layer(lid, token, referer)
        with open(out_path, "w") as fh:
            json.dump(fc, fh)
        n = len(fc["features"])
        print("     OK: %d feature(s) written" % n)
        summary.append((lid, name, n, "ok"))
    except Exception as e:
        print("     FAILED: %s" % e)
        summary.append((lid, name, 0, "FAILED"))

print("\n" + "=" * 64)
print("DONE. Summary:")
for lid, name, n, status in summary:
    print("  [%2d] %-30s %6s  %s" % (lid, name, n, status))
print("\nFiles in:", OUT_DIR)
print("NOTE: geometry is WGS84 (EPSG:4326). Reproject to your site UTM zone")
print("      (EPSG:26912 for AZ) before clipping to watershed boundaries.")

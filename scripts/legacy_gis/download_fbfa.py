# =============================================================================
# download_fbfa.py  (QGIS Python Console)
#
# Downloads all layers of the NHA "FBFA_Data" hosted FeatureServer to GeoJSON
# files in a subfolder under ROOT. Generates a portal token first (the query
# endpoint returns code 499 "Token Required" without one), then paginates each
# layer against MaxRecordCount.
#
# USAGE (QGIS Python Console):
#   exec(open("/home/arash/Dropbox/Chloeta/NHA/PythonScripts/download_fbfa.py").read())
#
# You will be prompted for your portal password (not echoed). Username defaults
# to PORTAL_USER below -- change if needed.
# =============================================================================

import os
import json
import time
import getpass
import urllib.parse
import urllib.request

# --- settings --------------------------------------------------------------

ROOT     = "/home/arash/Dropbox/Chloeta/NHA"
OUT_DIR  = os.path.join(ROOT, "FBFA_download")

PORTAL    = "https://nhagisportal.com/portal"
TOKEN_URL = PORTAL + "/sharing/rest/generateToken"
PORTAL_USER = "Arashm"          # change if your login differs

SERVICE = ("https://nhagisportal.com/arcgis/rest/services/"
           "Hosted/FBFA_Data/FeatureServer")

LAYERS = {
    6:  "all_points",
    7:  "cmp_culverts",
    8:  "buildings",
    9:  "water_tanks",
    10: "all_lines",
    11: "bf_bridges",
    12: "bf_roads",
    13: "official_bf_boundary",
}

OUT_SR    = 4326
PAGE_SIZE = 2000
TIMEOUT   = 120

# ---------------------------------------------------------------------------

os.makedirs(OUT_DIR, exist_ok=True)

def post(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "QGIS-FBFA-dl"})
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
    raw = post(TOKEN_URL, params)
    obj = json.loads(raw)
    if "token" not in obj:
        raise RuntimeError("Token request failed: %s" % obj)
    return obj["token"], params["referer"]

def query_layer(layer_id, token, referer):
    base = "%s/%d/query" % (SERVICE, layer_id)
    features = []
    offset = 0
    crs_block = None
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": OUT_SR,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "true",
            "token": token,
        }
        url = base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": "QGIS-FBFA-dl",
            "Referer": referer,
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")

        try:
            gj = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("Layer %d: non-JSON response:\n%s"
                               % (layer_id, raw[:300]))
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

# --- run -------------------------------------------------------------------

print("=" * 64)
print("FBFA_Data download -> %s" % OUT_DIR)
print("=" * 64)

def get_password():
    # 1) honor a preset variable: set FBFA_PW = "..." in the console first
    try:
        if FBFA_PW:
            return FBFA_PW
    except NameError:
        pass
    # 2) QGIS GUI: masked input dialog (no echo, nothing stored)
    try:
        from qgis.PyQt.QtWidgets import QInputDialog, QLineEdit
        from qgis.utils import iface
        parent = iface.mainWindow() if iface else None
        pw, ok = QInputDialog.getText(
            parent, "FBFA download",
            "Portal password for %s:" % PORTAL_USER,
            QLineEdit.Password)
        if not ok or not pw:
            raise RuntimeError("Password entry cancelled.")
        return pw
    except ImportError:
        # 3) plain terminal fallback
        return getpass.getpass("Portal password for %s: " % PORTAL_USER)

pw = get_password()
print("Requesting token...")
token, referer = get_token(PORTAL_USER, pw)
print("Token acquired (expires in ~120 min).")

summary = []
for lid, name in LAYERS.items():
    out_path = os.path.join(OUT_DIR, "%s.geojson" % name)
    print("\n[%2d] %-22s -> %s" % (lid, name, os.path.basename(out_path)))
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
    print("  [%2d] %-22s %6s  %s" % (lid, name, n, status))
print("\nFiles in:", OUT_DIR)
print("NOTE: geometry is WGS84 (EPSG:4326). Reproject to your site UTM zone")
print("      before clipping to watershed boundaries.")
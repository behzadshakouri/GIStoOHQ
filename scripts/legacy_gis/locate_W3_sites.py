#!/usr/bin/env python3
# =============================================================================
# locate_ws3_sites.py
#
# Locate the WS3 priority-matrix sites using the NHA GeoJSON layers. Each site
# is identified by its Project No. (e.g. AZ12-100), matched against the
# 'project_number' field in the NHA Structures layer. For each site we compute
# a centroid (mean of its structure points), the spatial extent, a structure
# count, and the containing Chapter / HMO. Output:
#
#   ws3_site_locations.csv       one row per site (review table)
#   ws3_site_locations.geojson   one point per site at its centroid (for Nathan)
#   ws3_site_locations_map.png   overview map of all sites (AZ/NM)
#
# Matching handles SHARED PARCELS: some structures store a combined project
# number like "NM15-131/NM15-134". We split on '/' and test membership, so a
# site is matched even when it shares a parcel record. Such matches are flagged
# match_type='shared_parcel' (their centroid is approximate, since the parcel's
# structures belong to more than one project).
#
# USAGE:
#   python3 locate_ws3_sites.py
# =============================================================================
import os
import re
import csv
import json

# --- inputs / outputs ------------------------------------------------------
UPLOAD_DIR = "/mnt/user-data/uploads"
OUT_DIR    = "/mnt/user-data/outputs"

STRUCTURES = os.path.join(UPLOAD_DIR, "00_NHA_Structures.geojson")
SUBDIVS    = os.path.join(UPLOAD_DIR, "05_Subdivisions.geojson")
HMO        = os.path.join(UPLOAD_DIR, "06_HMO_Boundaries.geojson")
CHAPTERS   = os.path.join(UPLOAD_DIR, "07_Chapter_Boundaries.geojson")
MATRIX     = os.path.join(UPLOAD_DIR, "WS3_Priority_Matrix_17jun26.xlsx")

CSV_OUT  = os.path.join(OUT_DIR, "ws3_site_locations.csv")
GJ_OUT   = os.path.join(OUT_DIR, "ws3_site_locations.geojson")
MAP_OUT  = os.path.join(OUT_DIR, "ws3_site_locations_map.png")


# --- helpers ---------------------------------------------------------------
def load_geojson(path):
    with open(path) as fh:
        return json.load(fh).get("features", [])


def point_xy(feat):
    """(lon, lat) from a point feature's geometry, falling back to long/lat attrs."""
    g = feat.get("geometry") or {}
    if g.get("type") == "Point" and g.get("coordinates"):
        c = g["coordinates"]
        return float(c[0]), float(c[1])
    p = feat.get("properties", {})
    if p.get("long") is not None and p.get("lat") is not None:
        return float(p["long"]), float(p["lat"])
    return None


def split_proj(value):
    """Project numbers in a (possibly combined) field -> set of individual IDs."""
    if value is None:
        return set()
    return {tok.strip() for tok in str(value).split("/") if tok.strip()}


def ring_contains(x, y, ring):
    """Ray-casting point-in-polygon for a single ring (list of [x,y])."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def poly_contains(x, y, geom):
    """Point in Polygon/MultiPolygon (outer ring test; holes ignored -- fine
       for chapter/HMO boundaries)."""
    if not geom:
        return False
    t = geom.get("type")
    coords = geom.get("coordinates")
    if t == "Polygon":
        return ring_contains(x, y, coords[0])
    if t == "MultiPolygon":
        return any(ring_contains(x, y, poly[0]) for poly in coords)
    return False


def find_containing(x, y, polys, name_field):
    for f in polys:
        if poly_contains(x, y, f.get("geometry")):
            return f.get("properties", {}).get(name_field)
    return None


# --- load data -------------------------------------------------------------
print("Loading layers...")
structures = load_geojson(STRUCTURES)
chapters   = load_geojson(CHAPTERS)
hmos       = load_geojson(HMO)
print("  structures: %d | chapters: %d | hmos: %d"
      % (len(structures), len(chapters), len(hmos)))

# index structures by each individual project id they belong to
import collections
by_proj = collections.defaultdict(list)   # proj_id -> [(x,y,is_shared), ...]
for f in structures:
    raw = f.get("properties", {}).get("project_number")
    ids = split_proj(raw)
    if not ids:
        continue
    xy = point_xy(f)
    if xy is None:
        continue
    shared = len(ids) > 1
    for pid in ids:
        by_proj[pid].append((xy[0], xy[1], shared))

# --- read the priority matrix ----------------------------------------------
import pandas as pd
df = pd.read_excel(MATRIX)
# rows with a real project number (AZ../NM..); skip the GROUP header rows
df = df[df["Project No."].astype(str).str.match(r"^(AZ|NM)\d", na=False)].copy()
df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
proj_col = "Project No."
loc_col  = "Location / Community"
work_col = "Work Type"
tier_col = [c for c in df.columns if c.lower().startswith("priority")][0]

print("Matrix sites:", len(df))

# --- locate each site ------------------------------------------------------
rows = []
for _, r in df.iterrows():
    pid   = str(r[proj_col]).strip()
    comm  = str(r.get(loc_col, "")).strip()
    work  = str(r.get(work_col, "")).strip()
    tier  = r.get(tier_col)
    pts   = by_proj.get(pid, [])
    n     = len(pts)
    if n == 0:
        rows.append({
            "project_no": pid, "community": comm, "work_type": work,
            "priority_tier": tier, "n_structures": 0, "match_type": "no_match",
            "lon": None, "lat": None, "lon_min": None, "lon_max": None,
            "lat_min": None, "lat_max": None, "chapter": None, "hmo": None,
        })
        continue
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    cx = sum(xs) / n; cy = sum(ys) / n
    any_shared = any(p[2] for p in pts)
    all_shared = all(p[2] for p in pts)
    match_type = ("shared_parcel" if all_shared
                  else ("mixed_shared" if any_shared else "exact"))
    chapter = find_containing(cx, cy, chapters, "name")
    hmo     = find_containing(cx, cy, hmos, "hmo")
    rows.append({
        "project_no": pid, "community": comm, "work_type": work,
        "priority_tier": tier, "n_structures": n, "match_type": match_type,
        "lon": round(cx, 7), "lat": round(cy, 7),
        "lon_min": round(min(xs), 7), "lon_max": round(max(xs), 7),
        "lat_min": round(min(ys), 7), "lat_max": round(max(ys), 7),
        "chapter": chapter, "hmo": hmo,
    })
    flag = "" if match_type == "exact" else "  [%s]" % match_type
    print("  %-10s n=%-4d (%.5f, %.5f)%s" % (pid, n, cy, cx, flag))

# --- write CSV -------------------------------------------------------------
os.makedirs(OUT_DIR, exist_ok=True)
cols = ["project_no", "community", "work_type", "priority_tier",
        "n_structures", "match_type", "lat", "lon",
        "lat_min", "lat_max", "lon_min", "lon_max", "chapter", "hmo"]
with open(CSV_OUT, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    for row in rows:
        w.writerow(row)
print("wrote", CSV_OUT)

# --- write points GeoJSON (one point per located site) ---------------------
feats = []
for row in rows:
    if row["lon"] is None:
        continue
    feats.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
        "properties": {k: row[k] for k in cols},
    })
fc = {
    "type": "FeatureCollection",
    "crs": {"type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
    "features": feats,
}
with open(GJ_OUT, "w") as fh:
    json.dump(fc, fh, indent=1)
print("wrote %s (%d points)" % (GJ_OUT, len(feats)))

# --- map -------------------------------------------------------------------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 8))
    # faint chapter boundaries for context
    for f in chapters:
        g = f.get("geometry") or {}
        polys = ([g["coordinates"]] if g.get("type") == "Polygon"
                 else g.get("coordinates", []) if g.get("type") == "MultiPolygon"
                 else [])
        for poly in polys:
            ring = poly[0]
            ax.plot([p[0] for p in ring], [p[1] for p in ring],
                    color="#cccccc", lw=0.4, zorder=1)

    tier_color = {1: "#C0392B", 2: "#E67E22", 3: "#3E6B96", 4: "#5B8C5A"}
    for row in rows:
        if row["lon"] is None:
            continue
        try:
            t = int(row["priority_tier"])
        except (TypeError, ValueError):
            t = 0
        c = tier_color.get(t, "#666666")
        m = "^" if row["match_type"] != "exact" else "o"
        ax.scatter(row["lon"], row["lat"], s=70, c=c, marker=m,
                   edgecolors="black", linewidths=0.6, zorder=3)
        ax.annotate(row["project_no"], (row["lon"], row["lat"]),
                    textcoords="offset points", xytext=(5, 4),
                    fontsize=7, color="#1F3864")

    # legend
    from matplotlib.lines import Line2D
    leg = [Line2D([0], [0], marker="o", color="w", markerfacecolor=tier_color[t],
                  markeredgecolor="black", markersize=9, label="Tier %d" % t)
           for t in sorted(tier_color)]
    leg.append(Line2D([0], [0], marker="^", color="w", markerfacecolor="#999",
                      markeredgecolor="black", markersize=9,
                      label="shared parcel (approx.)"))
    ax.legend(handles=leg, loc="lower left", fontsize=8, framealpha=0.9)

    ax.set_title("WS3 Priority Sites -- located from NHA Structures",
                 color="#1F3864", fontsize=13, fontweight="bold")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_aspect(1.25)
    ax.grid(True, color="#eeeeee", lw=0.5)
    plt.tight_layout()
    plt.savefig(MAP_OUT, dpi=150)
    print("wrote", MAP_OUT)
except Exception as e:
    print("map skipped:", e)

# --- summary ---------------------------------------------------------------
located = [r for r in rows if r["lon"] is not None]
shared  = [r for r in rows if r["match_type"] in ("shared_parcel", "mixed_shared")]
nomatch = [r for r in rows if r["match_type"] == "no_match"]
print("\nSUMMARY: %d sites | %d located | %d shared-parcel | %d unmatched"
      % (len(rows), len(located), len(shared), len(nomatch)))
if nomatch:
    print("  unmatched:", ", ".join(r["project_no"] for r in nomatch))
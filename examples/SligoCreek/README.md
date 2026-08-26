# Sligo Creek DEM workflow smoke example

This folder is a small no-network smoke test for the outlet-first DEM acquisition workflow. It uses simplified demo GeoJSON files, not authoritative hydrography or DEM indexes.

## Outlet and study extent

The watershed-data handoff SiteSpec at `sites/sligocreekdemo.yaml` records the
current modeling outlet and its GIStoOHQ-delineated catchment area of **23.7546
km²** (`23,754,600 m²`). HydroPINN exports use that explicit modeled-catchment
value; they do not substitute the areas of the separate review geometries below.

`SC.kmz` contains the original Google Earth point marker at **38.95840888229726, -76.97391566325376**; routing-grid review found that this marker required a 142.50 m move. `Estimated Sligo Creek.kmz` contains a closed Google Earth `LineString` outline named **Estimated SC**, not an outlet point. Its approximate spherical area is **45.8 km² (17.7 mi²)**, so treat it as an operator-digitized review/reference outline whose suitability depends on the intended “real Sligo Creek” reference area and expected ~20% tolerance. The workflow now uses an upstream Sligo Creek outlet candidate **38.9700, -77.0000** (EPSG:26918 approximately **326697.74, 4315356.34**) that lies inside the estimated review outline, avoiding the downstream Northwest Branch confluence used by the original marker. Verify the final outlet against authoritative hydrography before design use.

The example config records that KMZ under `documented_watershed`, so a UI **FULL RUN** passes it into `full-run` and writes `watershed_documented_comparison.json` after delineation. In the run shown during review, the DEM-derived watershed area was **39.08 km²** versus the KMZ estimate of **45.77 km²**, a **-14.6%** difference relative to the KMZ estimate; that is within a ~20% area-screening tolerance, while the WBD HUC12 remains regional context rather than a Sligo Creek boundary. To process the bundled estimate manually, import it as the documented watershed reference after confirming that the modeled outlet lies inside the outline:

```bash
ohqbuild import-watershed-reference \
  --root examples/SligoCreek --site SligoCreekDemo \
  --source "examples/SligoCreek/Estimated Sligo Creek.kmz" \
  --lon -77.0000 --lat 38.9700 \
  --source-title "Estimated Sligo Creek review outline" \
  --source-organization "Operator digitized Google Earth review" \
  --source-url "examples/SligoCreek/Estimated Sligo Creek.kmz" \
  --license "project review artifact; verify before design use"
```

The importer converts a closed KML/KMZ `LineString` into a derived polygon and records that provenance. The corrected example outlet is expected to be contained by this review outline; if containment fails, stop and move the outlet upstream on Sligo Creek rather than using the outside-outlet override for production Sligo runs. The importer still rejects open lines, points, images, and PDFs as watershed boundaries.

The default acquisition envelope now starts from `Estimated Sligo Creek.kmz` and
expands that documented outline by a **2 km uncertainty margin** on every side.
This keeps the public-review watershed estimate as the DEM coverage driver while
allowing for operator digitizing error, outlet uncertainty, and DEM-routing edge
effects. This is acquisition padding, not a request to model the downstream
Northwest Branch or the whole Anacostia basin. After DEM delineation, inspect the
boundary and retain only the drainage area upstream of the Sligo-side pour point.
See `outlet_and_extent.geojson` for the machine-readable point and review notes.

If **FULL RUN** is pressed without drawing an area, the UI first regenerates
`intermediate/dem_acquisition_area.geojson` from this configured KMZ-derived
envelope, then passes that file to `full-run`. A stale area from an earlier
configuration is therefore not silently reused.

The same refresh also occurs when `full-run --config ...` is called directly:
the configured outlet and acquisition KMZ files are reread before download and
clipping. The workflow summary records their SHA-256 digests and sizes, tying
each generated area and outlet to the exact KMZ revisions used by that run.

Public surface-water mapping supports a confluence check, but it cannot make the bundled synthetic centerline authoritative. Before design use, confirm/snap the candidate against current [USGS NLDI](https://api.water.usgs.gov/nldi/linked-data) or authoritative NHD/3DHP hydrography and visually confirm that the selected flowline is Sligo Creek rather than Northwest Branch.

Run the prepare path from the repository root. The wrapper uses `ohqbuild` when installed and falls back to `python -m ohqbuilder.cli` from a source checkout:

```bash
scripts/run_dem_prep.sh examples/SligoCreek/dem_workflow.example.yaml
```

Expected outputs are written under `examples/SligoCreek/`; generated smoke-test outputs are ignored by the example `.gitignore`:

```text
inputs/outlet_raw.geojson
inputs/outlet_snapped.geojson
intermediate/dem_acquisition_area.geojson
intermediate/dem_download_manifest.json
intermediate/dem_workflow_summary.json
```

The demo tile index includes one intersecting tile and one outside tile, so the generated manifest should select only `dem/raw/demo_tile_sligo_01.tif`.

For real Sligo Creek work, replace:

- `hydro/NHDFlowline.demo.geojson` with a real EPSG:4326 flowline GeoJSON near the outlet.
- `indexes/usgs_3dep_tiles.demo.geojson` with a real DEM tile footprint/index GeoJSON containing `url` and/or `path` fields.

Then run:

```bash
scripts/run_dem_prep.sh examples/SligoCreek/dem_workflow.example.yaml --download --materialize
```

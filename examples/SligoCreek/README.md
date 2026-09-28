# Sligo Creek DEM workflow smoke example

This folder is a small no-network smoke test for the outlet-first DEM acquisition workflow. It uses simplified demo GeoJSON files, not authoritative hydrography or DEM indexes.

## Outlet and study extent

The watershed-data handoff SiteSpecs record the reviewed modeling outlet and an
accepted GIStoOHQ-delineated catchment area of **23.67 km²** (`23,670,000 m²`).
The latest whole-watershed geometry measures **23.6653 km²**, which rounds to
that accepted value. HydroPINN exports use the explicit modeled-catchment value;
they do not substitute the areas of the separate acquisition geometries below.

`SC outlet.kmz` supplies the reviewed outlet at approximately **38.9783762,
-76.9887379**. `Estimated Sligo Creek.kmz` contains a closed Google Earth
`LineString` outline named **Estimated SC**. Its current geometry is an
operator estimate of roughly **34.30 km²** used to choose and pad the DEM
acquisition area. It is not the accepted watershed boundary and is not expected
to match 23.67 km².

The example config records the KMZ with `role: acquisition_estimate`. A UI
**FULL RUN** still writes `watershed_documented_comparison.json` so the coverage
geometry can be inspected, but that comparison is informational and is excluded
from the delineation-confidence rating. The WBD HUC12 remains regional context
rather than a Sligo Creek boundary. To process the bundled estimate manually:

```bash
ohqbuild import-watershed-reference \
  --root examples/SligoCreek --site SligoCreekDemo \
  --source "examples/SligoCreek/Estimated Sligo Creek.kmz" \
  --lon -76.9887378616 --lat 38.9783761807 \
  --source-title "Estimated Sligo Creek review outline" \
  --source-organization "Operator digitized Google Earth review" \
  --source-url "examples/SligoCreek/Estimated Sligo Creek.kmz" \
  --license "project acquisition estimate" \
  --reference-role acquisition_estimate
```

The importer converts a closed KML/KMZ `LineString` into a derived polygon and
records its provenance and role. The example outlet is expected to be contained
by this acquisition estimate. The importer still rejects open lines, points,
images, and PDFs as watershed boundaries.

The default acquisition envelope now starts from `Estimated Sligo Creek.kmz` and
expands that documented outline by a **2 km uncertainty margin** on every side.
This keeps the operator's acquisition estimate as the DEM coverage driver while
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

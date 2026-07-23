# Sligo Creek DEM workflow smoke example

This folder is a small no-network smoke test for the outlet-first DEM acquisition workflow. It uses simplified demo GeoJSON files, not authoritative hydrography or DEM indexes.

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

# Sligo Creek DEM workflow smoke example

This folder is a small no-network smoke test for the outlet-first DEM acquisition workflow. It uses simplified demo GeoJSON files, not authoritative hydrography or DEM indexes.

## Outlet and study extent

`SC.kmz` places the outlet candidate at **38.95840888229726, -76.97391566325376**, at the Sligo Creek side of its confluence with Northwest Branch. The workflow and the legacy downloader CSV now use that same coordinate. It replaces the former `39.000215, -77.010810` point, which is upstream within the watershed and therefore was not a watershed mouth.

The acquisition envelope follows the Sligo centerline and uses **1,000 m** upstream, downstream, and lateral margins. This is acquisition padding, not a request to model the downstream Northwest Branch or the whole Anacostia basin. After DEM delineation, inspect the boundary and retain only the drainage area upstream of the Sligo-side pour point. See `outlet_and_extent.geojson` for the machine-readable point and review notes.

If **FULL RUN** is pressed without drawing an area, the UI first regenerates
`intermediate/dem_acquisition_area.geojson` from these configured network and
margin values, then passes that file to `full-run`. A stale area from an earlier
configuration is therefore not silently reused.

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

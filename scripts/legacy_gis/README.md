# Legacy GIS scripts

These are the uploaded QGIS / preprocessing scripts retained for reference and reproducibility.

The new OHQ package consumes the outputs they produce:

- `topology.gpkg`
- `subwatershed_params.gpkg`
- `reaches.gpkg`
- `junctions.gpkg`

The central rule remains: `topology.gpkg` is the single source of truth for connectivity.

See `docs/input_generation.md` for the phase-1/phase-2 QGIS workflow that creates
these files.

# Workflow

## Phase A — GIS preprocessing

Use the retained QGIS scripts to produce the GeoPackage inputs consumed by
GIStoOHQ:

- `watershed_boundary.gpkg`
- `reaches.gpkg`
- `junctions.gpkg`
- `subwatersheds.gpkg`
- `subwatershed_params.gpkg`
- `topology.gpkg`

For the detailed input-generation workflow, see
[`docs/input_generation.md`](input_generation.md).

## Phase B — OHQ generation

```bash
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100
```

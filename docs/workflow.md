# Workflow

## Phase A — GIS preprocessing

Use the retained QGIS scripts to produce:

- `watershed_boundary.gpkg`
- `reaches.gpkg`
- `junctions.gpkg`
- `subwatersheds.gpkg`
- `subwatershed_params.gpkg`
- `topology.gpkg`

## Phase B — OHQ generation

```bash
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100
```

# GIStoOHQ

GIStoOHQ converts GIS-derived watershed products into OpenHydroQual (`.ohq`) model files.

```text
GIS preprocessing
    ↓
hydrologic/geometric attributes
    ↓
topology.gpkg as the single source of truth
    ↓
internal watershed model
    ↓
OpenHydroQual writer
    ↓
<SITE>.ohq
```

## Main inputs

```text
<SITE>/outputs/topology.gpkg
<SITE>/outputs/subwatershed_params.gpkg
<SITE>/outputs/reaches.gpkg
<SITE>/outputs/junctions.gpkg
```

## Quick start

```bash
pip install -e .
ohqbuild doctor
ohqbuild check-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100

# Or copy config.example.json to config.json and run the whole app pipeline:
python3 run.py config.json
```

Need to create those GIS input files first? Run the full workflow with
`ohqbuild run` from a QGIS Python environment, or run the steps individually with
`ohqbuild prepare-inputs`, `ohqbuild check-inputs`, and `ohqbuild build`. The
`build` and `validate` commands also check inputs by default. See
[`docs/input_generation.md`](docs/input_generation.md).

When running both preparation phases, GIStoOHQ automatically creates
`outputs/pour_points.shp` from the Phase 1 junction network before Phase 2. The
same operation is available independently with `ohqbuild create-pour-points`.

The output is written to:

```text
/path/to/NHA/WS3_GIS/AZ12-100/outputs/AZ12_100.ohq
```

## Design rule

`topology.gpkg` is the authoritative source of connectivity. The OHQ writer does not infer or rewrite topology.

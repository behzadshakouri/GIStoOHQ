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

To try the new outlet-first DEM workflow UI immediately from a source checkout,
run the bundled launcher script. It uses `ohqbuild ui` when installed and falls
back to `python -m ohqbuilder.cli ui`, so it works before packaging:

```bash
scripts/run_dem_ui.sh
```

The Tk launcher includes an OpenStreetMap tile picker for choosing the outlet
coordinate interactively. It uses public OSM raster tiles when network access is
available, caches tiles after first load, supports zooming and right-click
recentering, and writes the clicked coordinate back to the outlet
longitude/latitude fields. The QGIS plugin uses the
active QGIS map canvas instead, so users can pick points against any basemap or
GIS layers they have loaded there. If the demo YAML is left with merge-conflict
markers after a branch update, use **reset Sligo demo** in the Tk launcher to
rewrite the bundled demo config while preserving the current outlet coordinate.

For a no-network smoke test of the DEM prep path, run the Sligo Creek demo:

```bash
scripts/run_dem_prep.sh examples/SligoCreek/dem_workflow.example.yaml
```

For a single command that starts from an approximate outlet coordinate, downloads
source data, materializes the DEM and NHD flowlines, runs both GIS phases, and
writes the final OHQ file, use a QGIS Python environment:

### Run the complete workflow

From the repository root, install the package and GIS extras into the Python
environment used by QGIS:

```bash
cd /path/to/GIStoOHQ
python -m pip install -e '.[gis]'
ohqbuild doctor --strict-gis
```

Then provide a project root, a site directory relative to that root, and an
approximate WGS84 outlet coordinate:

```bash
ohqbuild full-run --root /path/to/NHA --site WS3_GIS/AZ12-100 \
  --lat 34.123 --lon -111.456 \
  --buffer 5000
```

The final file is written to `<ROOT>/<SITE>/outputs/<SITE>.ohq` unless `--out`
is supplied. Use `ohqbuild full-run --help` to see source-directory, tile-limit,
maximum-file-size, target-CRS, and soil-resolution options. The downloader checks
existing files against TNM size metadata, skips valid cached files, and
redownloads incomplete/corrupt files.

`full-run` uses GIStoOHQ's built-in Python TNM downloader; compiling or installing
the external C++ `demcheck` program is not required. It runs the complete
four-step workflow: download all supported inputs (DEM, hydrography, HSG, and
soil texture), merge/clip source products, generate GIS inputs, then validate and
write the OHQ file. The corresponding staged commands are `download-inputs`,
`materialize-inputs`, `prepare-inputs`, and `build`.

To inspect or rerun individual stages, use:

```bash
ohqbuild download-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100 \
  --lat 34.123 --lon -111.456 --buffer 5000
ohqbuild materialize-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100
ohqbuild prepare-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100
```

The download stages require network access. Materialization requires the GIS
extras, and `prepare-inputs`/`full-run` require QGIS plus its `processing` plugin.

### Run from the config-driven script

`run.py` now supports both layouts. Copy and edit one of the supplied files:

```bash
# One pipeline command (`ohqbuild full-run`)
cp config.one-step.example.json my-run.json
python3 run.py my-run.json

# Four explicit commands (download, materialize, prepare, build)
cp config.four-step.example.json my-four-steps.json
python3 run.py my-four-steps.json
```

Set `workflow` to `one-step` or `four-step`; both require `lat` and `lon`.
Use `python3 run.py my-run.json --dry-run` to print the commands without running
them. To check both supported start-to-finish layouts without network or QGIS,
run `python3 scripts/check_run_workflows.py`; it dry-runs the one-step and
four-step example configs and verifies every expected stage is present. The
original config behavior remains available with `workflow: legacy`.

The existing three-step workflow remains available for controlled or offline runs.

Need to create those GIS input files first? Run the full workflow with
`ohqbuild run` from a QGIS Python environment, or run the steps individually with
`ohqbuild prepare-inputs`, `ohqbuild check-inputs`, and `ohqbuild build`. The
`build` and `validate` commands also check inputs by default. See
[`docs/input_generation.md`](docs/input_generation.md).

When running both preparation phases, GIStoOHQ automatically creates
`outputs/pour_points.shp` from the Phase 1 junction network before Phase 2. The
same operation is available independently with `ohqbuild create-pour-points`.
If `outputs/outlet.shp` is missing, Phase 1 now creates it automatically at the
largest valid cell in `outputs/flow_acc.tif`. Use `ohqbuild create-outlet` to run
that operation independently.

The output is written to:

```text
/path/to/NHA/WS3_GIS/AZ12-100/outputs/AZ12_100.ohq
```

## Design rule

`topology.gpkg` is the authoritative source of connectivity. The OHQ writer does not infer or rewrite topology.

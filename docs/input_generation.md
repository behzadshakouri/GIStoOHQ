# Creating GIStoOHQ input files

GIStoOHQ does **not** delineate watersheds itself. It consumes GIS-derived
GeoPackage outputs that are produced upstream, usually in QGIS with the retained
legacy preprocessing scripts.

The four files required by `ohqbuild build` are:

```text
<ROOT>/<SITE>/outputs/topology.gpkg
<ROOT>/<SITE>/outputs/subwatershed_params.gpkg
<ROOT>/<SITE>/outputs/reaches.gpkg
<ROOT>/<SITE>/outputs/junctions.gpkg
```

`topology.gpkg` is the authoritative connectivity table. GIStoOHQ reads it and
does not infer or rewrite connectivity.

## Recommended source workflow

The retained scripts under `scripts/legacy_gis/` are QGIS Python Console scripts.
They are kept for reproducibility and for producing the input files that the
packaged GIStoOHQ builder consumes.

### Phase 0 — check the environment

Before preparing inputs, check the installed runtime, GIS dependencies, QGIS
`processing` plugin, and legacy script availability:

```bash
ohqbuild doctor
```

Use `--strict-gis` when you want missing QGIS bindings or the QGIS `processing`
plugin to fail the check instead of showing warnings:

```bash
ohqbuild doctor --strict-gis
```

For CI logs or automation, emit machine-readable diagnostics:

```bash
ohqbuild doctor --json
```

### Phase 0 — prepare site inputs

To create the expected folders and an `INPUTS.md` checklist, run:

```bash
ohqbuild init-inputs --root /mnt/3rd900/Projects/GIStoOHQ --site .
```

Before running the QGIS workflow, the site directory should contain:

```text
<ROOT>/
└── <SITE>/
    ├── demlr/
    │   └── cliped_utm.tif
    └── outputs/
        ├── outlet.shp
        └── NHDFlowline_clip.gpkg
```

The legacy phase-1 runner expects:

- `outputs/outlet.shp` — single-feature watershed outlet.
- `demlr/cliped_utm.tif` — real-elevation DEM.
- `outputs/NHDFlowline_clip.gpkg` — clipped NHD flowlines used for channel
  burning and reach extraction.

If `outputs/outlet.shp` is absent, `prepare-inputs` derives it automatically at
the center of the maximum valid flow-accumulation cell. The same operation can
be run explicitly:

```bash
ohqbuild create-outlet --root /path/to/NHA --site WS3_GIS/AZ12-100
```

Use `--no-auto-outlet` to require a supplied outlet instead, or
`create-outlet --overwrite` to intentionally replace an existing outlet.

If DEM or flowline inputs are missing, see [`docs/data_downloaders.md`](data_downloaders.md)
for notes on using DEMDownloader/`demcheck` upstream.

### Phase 1 — create reach and junction network

Run the phase-1 orchestrator from a QGIS Python environment:

```bash
ohqbuild prepare-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100 --phase phase1
```

This command executes `scripts/legacy_gis/run_phase1.py` with `ROOT`, `SITE_DIR`,
and `SCRIPT_DIR` set for the legacy scripts. If your scripts are outside the
installed repo, pass `--script-dir /path/to/scripts/legacy_gis`.

Phase 1 runs the reach/junction preprocessing scripts in order:

1. `clip_only.py`
2. `fillsink_etc.py`
3. `delineate_whole_watershed.py`
4. `clip_dem_to_watershed.py`
5. `extract_reaches.py`
6. `derive_topology_reaches.py`
7. `materialize_junctions.py`

Expected phase-1 outputs include:

```text
<ROOT>/<SITE>/outputs/watershed_boundary.gpkg
<ROOT>/<SITE>/outputs/reaches.gpkg
<ROOT>/<SITE>/outputs/junctions.gpkg
```

After phase 1, pour points are generated automatically from the Phase 1 junction
network when `prepare-inputs --phase all` advances to Phase 2. To create or
inspect them as a separate step, run:

```bash
ohqbuild create-pour-points --root /path/to/NHA --site WS3_GIS/AZ12-100
```

This writes deterministic `id` and `name` fields to:

```text
<ROOT>/<SITE>/outputs/pour_points.shp
```

### Phase 2 — create subbasin parameters and topology

Then run the phase-2 orchestrator from a QGIS Python environment:

```bash
ohqbuild prepare-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100 --phase phase2
```

Pass `--no-auto-pour-points` to `prepare-inputs` or `run` to retain a manually
created file. Existing pour points are never overwritten automatically; use
`create-pour-points --overwrite` when replacement is intentional.

To run both phases in sequence, use the default `all` phase:

```bash
ohqbuild prepare-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100
```

Phase 2 consumes the phase-1 reach/junction network and creates the subbasin
parameter and topology inputs:

1. `delineatewatershed.py`
2. `subtractsubwatershed.py`
3. `load_cn_inputs.py`
4. `cliptowatershed.py`
5. `prepcngrid.py`
6. `buildcnraster.py`
7. `zonal_cn.py`
8. `extract_slope.py`
9. `longestflowpath.py`
10. `compute_tc.py`
11. `build_topology.py`

The final files needed by GIStoOHQ should now exist:

```text
<ROOT>/<SITE>/outputs/topology.gpkg
<ROOT>/<SITE>/outputs/subwatershed_params.gpkg
<ROOT>/<SITE>/outputs/reaches.gpkg
<ROOT>/<SITE>/outputs/junctions.gpkg
```

Before building, verify the files and expected fields:

```bash
ohqbuild check-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100
```

If you only want to check that the files exist, without opening GeoPackage
layers, use:

```bash
ohqbuild check-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100 --no-schema
```

For automation, emit the validation result as JSON:

```bash
ohqbuild check-inputs --root /path/to/NHA --site WS3_GIS/AZ12-100 --json
```

## Required fields consumed by GIStoOHQ

The package readers use a small subset of fields from those GeoPackages.
Additional columns are preserved as attributes where applicable.

### `topology.gpkg`, layer `topology`

| Field | Purpose |
| --- | --- |
| `element_id` | Numeric ID of the source element within its type. |
| `element_type` | Source type such as `subbasin`, `reach`, or `junction`. |
| `name` | Source element name, e.g. `Subbasin_1`. |
| `ds_type` | Downstream type such as `junction`, `reach`, or `sink`. |
| `ds_id` | Downstream numeric ID, or null for the outlet/sink. |
| `ds_name` | Downstream element name. |
| `match_dist_m` | Audit distance for subbasin-to-junction matching. |
| `note` | Optional audit note. |

### `subwatershed_params.gpkg`, layer `subwatershed_params`

| Field | Purpose |
| --- | --- |
| `id` | Subbasin ID. |
| `area_km2` | Drainage area. |
| `CN` | Curve number. |
| `slope_pct` | Average basin slope. |
| `flow_len_ft` | Longest/representative flow length. |
| `tc_min` | Time of concentration. |
| `lag_min` | SCS lag. |
| `centroid_x`, `centroid_y` | Subbasin centroid coordinates. |

### `reaches.gpkg`

| Field | Purpose |
| --- | --- |
| `reach_id` | Reach ID. |
| `length_m` | Reach length. |
| `slope_mm` | Reach slope value used by the current reader. |
| `base_w_m` | Trapezoid base width. |
| `side_z` | Trapezoid side slope. |
| `manning_n` | Manning roughness. |
| `z_up_m`, `z_dn_m` | Upstream/downstream elevations. |

### `junctions.gpkg`, layer `junctions`

| Field | Purpose |
| --- | --- |
| `junction_id` | Junction ID. |
| `x`, `y` | Junction coordinates. |

## Run the full workflow

For a config-file driven application entry point, run the command below from the
project/data folder. If `config.json` is missing, `run.py` creates it from
`config.example.json` with `root` set to the current folder and stops so you can
review it before rerunning:

```bash
python3 run.py config.json
```

Use `--dry-run` to print the planned commands without executing them:

```bash
python3 run.py config.json --dry-run
```

From a QGIS Python environment, you can also run preparation, input validation,
and OHQ generation directly through the CLI:

```bash
ohqbuild run --root /path/to/NHA --site WS3_GIS/AZ12-100
```

If inputs already exist and you only want to validate and build, skip the QGIS
preparation step:

```bash
ohqbuild run --root /path/to/NHA --site WS3_GIS/AZ12-100 --skip-prepare
```

## Run GIStoOHQ after generating inputs

If you prefer separate steps, once `check-inputs` reports success, build the OHQ
file from a shell. `build` also performs the same input check by default before
reading the GeoPackages:

```bash
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100
```

Validate without writing an OHQ file; this also checks inputs first:

```bash
ohqbuild validate --root /path/to/NHA --site WS3_GIS/AZ12-100
```

For advanced debugging only, skip the pre-build input check:

```bash
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100 --skip-input-check
```

## Current limitation

The `prepare-inputs` command is a thin wrapper around the retained QGIS scripts,
so it must run in a QGIS Python environment with QGIS processing dependencies
available. It intentionally keeps the original script logic as the source of
truth so the generated files match the legacy workflow. The stable package
boundary is still the four GeoPackage inputs listed above.

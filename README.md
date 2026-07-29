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
cd /path/to/GIStoOHQ
scripts/run_dem_ui.sh
```

The wrapper first uses `.venv/bin/ohqbuild` when the repository virtual
environment exists, then an `ohqbuild` found on `PATH`, and finally
`${PYTHON:-python3} -m ohqbuilder.cli ui`. You can also launch it directly:

```bash
cd /path/to/GIStoOHQ
source .venv/bin/activate
ohqbuild ui
```

The launcher is a desktop Tk application, so run it from a graphical terminal.
On Debian/Ubuntu, install `python3-tk` if Python reports that `tkinter` is missing.

The Tk launcher includes a multi-basemap tile picker for choosing the outlet
coordinate interactively. Choose OpenStreetMap roads, Esri World Imagery satellite
imagery, or OpenTopoMap topography from the **Basemap** menu. Tiles are cached in
separate provider directories, and the displayed attribution changes with the
selected source. The launcher intentionally does not scrape undocumented Google
tile URLs; Google layers can be configured in QGIS with the user's licensed Google
Maps service. The picker supports zooming and right-click
recentering, and writes the clicked coordinate back to the outlet
longitude/latitude fields. The QGIS plugin uses the
active QGIS map canvas instead, so users can pick points against any basemap or
GIS layers they have loaded there. If the demo YAML is left with merge-conflict
markers after a branch update, use **reset Sligo demo** in the Tk launcher to
rewrite the bundled demo config while preserving the current outlet coordinate.
The cyan/blue line in the Tk map is the configured hydrography flowline overlay;
it is used for outlet snapping and visual channel reference, not as a rectangle edge.
The same map window can draw a rectangular acquisition area with two clicks or a
custom polygon with three or more clicks and **Finish area**. Drawn areas are saved
as EPSG:4326 GeoJSON and switch the DEM method to `polygon`. After DEM acquisition,
the **Create final OHQ file** section exposes the existing `prepare-hydrology`,
`prepare-inputs`, `check-inputs`, and `build` terminal stages.
**Continue automatically to OHQ** first creates `flow_dir.tif` and `flow_acc.tif`,
then runs the combined `ohqbuild run` workflow, which creates the outlet before
running the GIS phases and final build. The launcher prevents overlapping commands,
so each stage finishes before another can start; **STOP** terminates the active
command and its child process group when a long download or GIS run must be cancelled.
For a new real site, **FULL RUN: download all data to OHQ** invokes the terminal
`full-run` pipeline with the form's verified outlet, root, site, CRS, and download
directory. That pipeline downloads DEM, NHDPlus HR/NHD hydrography (including its
WBDHU reference layers), roads, land cover, Atlas 14,
hydrologic soil groups, and soil texture; mosaics/clips the GIS sources; runs
hydrology plus phase 1 and phase 2; validates the HEC-HMS-style watershed/network
inputs; and writes the final OHQ file. QGIS processing and internet access are
required for this production path.

The WBDHU layer extracted from the hydro package is an **authoritative reference**, not automatically the
model boundary. HUC12 units are standardized drainage units and may contain several
named urban streams; their internal lines are not the paper-specific subcatchments
created at tributaries, gauges, or monitoring sites. For a U.S. project, compare the
DEM-derived outlet basin with WBD, snap the outlet to NHDPlus HR, and use NHDPlus
catchments or tributary junctions to subdivide it. Do not replace a named-creek basin
with the containing HUC12 unless their outlets and extents actually agree. See
[`docs/authoritative_watersheds.md`](docs/authoritative_watersheds.md) for the
recommended decision process and validation metrics.
When materialization bounds are available, `materialize-inputs` extracts the
intersecting HUC12 features to `outputs/WBDHU12_reference.gpkg`, ready to overlay
with the generated watershed in QGIS.
When the downloaded NHDPlus package includes catchment polygons,
`materialize-inputs` also writes `outputs/NHDPlusCatchment_clip.gpkg`. These retain
their source reach identifiers for subsequent upstream-network tracing; they are
not treated as HUC subdivisions.
When a rectangle, polygon, or expanded acquisition GeoJSON is active, full-run uses
its bounds both to enlarge every source-data query and to clip the materialized DEM
and hydrography. The outlet remains the routing outlet; the drawn area controls data
coverage rather than replacing the outlet. If the drawn area excludes the outlet,
full-run expands the clipping bounds with a 500 m outlet safety margin so the DEM,
flow-direction, and flow-accumulation rasters remain consistent with the outlet.
The QGIS dock exposes the same full-run action and individual hydrology, GIS-input,
validation, and build stages, using the outlet selected on the active QGIS canvas.
The Tk launcher now exposes the NHDPlus snap limit, a **Use reviewed pour points**
toggle, and a **Promote reviewed pour points** action. The QGIS dock exposes the
same snap-limit and reviewed-point controls plus an explicit overwrite checkbox;
the plugin never overwrites promoted points merely because its promotion button
was clicked. Project keys `nhdplus_snap_distance_m` and
`use_reviewed_pour_points` remain supported as command defaults.
After moving `outputs/outlet.shp` in QGIS, select **Use edited outlet.shp** (or
pass `--use-existing-outlet`) so a subsequent full run uses that reviewed point
for acquisition and tracing instead of recreating it from stale longitude/latitude.
For a development install, run `scripts/install_qgis_plugin.sh`, restart QGIS,
enable **GIStoOHQ DEM Workflow**, and open it from the GIStoOHQ plugin menu. See
[`qgis_plugin/README.md`](qgis_plugin/README.md) for profiles, dependencies, and
basemap setup.
Use **Browse…** beside config and path fields to switch projects or folders. The
launcher also includes **Open Sligo example** and **Open John McCormack example**;
generated inputs, downloads, site outputs, and the final OHQ are written beneath the
selected Root shown in the form.
**Open generated layers in QGIS** starts the installed `qgis` executable and loads
all generated GeoTIFF, GeoPackage, Shapefile, and GeoJSON products beneath the
selected site's `demlr` and `outputs` directories, including the source DEM,
routing rasters, watershed, reaches, junctions, subwatersheds, and topology as they
become available.
**RUN RECOMMENDED NEXT STEP** inspects the selected project and chooses full-run,
hydrology, GIS preparation, OHQ build, or HEC-HMS build based on outputs that really
exist, avoiding manual execution of downstream stages before their prerequisites.
After phase 1 and phase 2 produce topology, subbasins, reaches, and junctions, the
**Build HEC-HMS** button writes native `.hms`, `.basin`, `.met`, `.control`, and
`.run` files under `<Root>/<Site>/outputs/hec_hms`; **Validate HEC-HMS** checks all
project references. Production full-run now creates both the OHQ and HEC-HMS file set.
If DEM validation reports `EXPAND` (exit code 3), use **Use expanded area** and
repeat preparation/download; the status is a refinement request rather than a crash.

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
is supplied. A review-ready `watershed_report.html` is written beside the OHQ file
with watershed counts, subbasin area, CN, slope, longest flow path, Tc, lag, and
links to the OHQ and HEC-HMS artifacts. The snapped outlet layer also records its
movement and a GREEN/YELLOW/RED quality rating so large automatic adjustments are
visible in QGIS. Use `ohqbuild full-run --help` to see source-directory, tile-limit,
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

# GIStoOHQ

GIStoOHQ turns GIS watershed data into connected hydrologic model projects for
[OpenHydroQual](https://openhydroqual.com/) and HEC-HMS.
It can start from an approximate outlet, acquire and prepare source data,
delineate a watershed and stream network, validate the resulting topology, and
write model files with traceable GIS-derived parameters.

## About

GIStoOHQ connects geospatial watershed preparation with process-based hydrologic
model construction. Its central design rule is simple: `topology.gpkg` is the
authoritative source of connectivity. Writers translate that reviewed topology
and its attributes into model formats; they do not invent or silently repair the
network.

[OpenHydroQual](https://openhydroqual.com/) is the modeling
engine and graphical environment used to open, inspect, edit, and run the
generated `.ohq` files. GIStoOHQ prepares model structure and GIS-derived initial
parameters; OpenHydroQual provides the process library and numerical simulation.

The project currently provides:

- outlet-first DEM acquisition, hydrologic conditioning, watershed delineation,
  subwatershed partitioning, and reach/junction extraction;
- U.S. source acquisition from USGS 3DEP and NHDPlus HR, NLCD, Census TIGER,
  NOAA Atlas 14, and USDA soil services;
- bounded international acquisition helpers for Copernicus GLO-30, OpenStreetMap
  hydrography and roads, SoilGrids texture, and reviewed land-cover crosswalks;
- OpenHydroQual models using legacy curve-number, standard HRU, mixed HRU, and
  two-aquifer riparian HRU formulations;
- optional rainfall, evapotranspiration, elevation-adjusted snow, alternative
  linear-reservoir baseflow, and reviewed DEM-derived channel segments;
- native HEC-HMS project generation and reference validation;
- watershed boundary, outlet, reach, partition, provenance, and regression
  reports for scientific review; and
- optional provider-neutral weather, discharge, PET/ET, forecast, and HydroPINN
  data packages.

```text
source data or reviewed GIS layers
              |
              v
DEM routing, watershed and stream extraction
              |
              v
validated topology.gpkg + spatial parameters
              |
              v
internal watershed model
              |
       +------+------+
       |             |
       v             v
OpenHydroQual      HEC-HMS
 (.ohq)          project files
```

GIStoOHQ is research software at version `0.1.0`. Generated models require review
of the outlet, watershed boundary, topology, parameters, forcing units, and
calibration before quantitative or design use.

## OpenHydroQual resources

| Resource | Link |
|---|---|
| Project website | [openhydroqual.com](https://openhydroqual.com/) |
| Documentation and tutorials | [OpenHydroQual knowledge base](https://openhydroqual.com/knowledge-base/) |
| Example models | [examples.openhydroqual.com](https://examples.openhydroqual.com/) |
| Video tutorials | [OpenHydroQual on YouTube](https://www.youtube.com/@openhydroqual218) |
| Source code and releases | [ArashMassoudieh/OpenHydroQual](https://github.com/ArashMassoudieh/OpenHydroQual) |
| Theory and equations | [2025 manual](https://openhydroqual.com/wp-content/UsersManual/OpenHydroQual_User_s_Manual.pdf) · [Google Scholar record](https://scholar.google.com/citations?view_op=view_citation&hl=en&user=0lTtuMoAAAAJ&citation_for_view=0lTtuMoAAAAJ:r0BpntZqJG4C) |
| Foundational software paper | [Massoudieh, Nguyen, and Murthy (2023)](https://doi.org/10.1016/j.envsoft.2023.105707) |

## Model outputs

A rain-only OpenHydroQual build writes four alternatives beside the requested
output path:

| Output | Formulation | Intended use |
|---|---|---|
| `*_legacy.ohq` | Curve-number catchment | Compatibility and event-model comparison |
| `*_standard_hru.ohq` | Homogeneous soil-column HRU | Local HRU process studies |
| `*_mixed_hru.ohq` | Pervious and impervious HRU | Urban land-cover partitioning |
| `*_riparian_hru.ohq` | Regional and near-stream aquifers | Cascaded hillslope/riparian groundwater |

When temperature forcing is enabled, GIStoOHQ writes snow-aware standard, mixed,
and riparian HRU models and omits the unsupported legacy CN/snow combination.
Setting a reviewed channel-profile file also writes
`*_detailed_mixed_hru.ohq` and `*_channel_fit.csv` without replacing the four
standard outputs.

The production pipeline can also create a native HEC-HMS project under
`outputs/hec_hms` and an HTML watershed report beside the generated models.
See [OHQ mapping](docs/ohq_mapping.md) for the exact formulation and forcing
rules.

## Requirements

- Python 3.9 or newer
- QGIS with Processing and GRASS providers for watershed preparation and a full
  source-to-model run
- Internet access for online source acquisition
- [OpenHydroQual](https://openhydroqual.com/) and its
  current resource library to execute generated `.ohq` models

Basic model building from existing GIS products does not require QGIS. Install a
development checkout with:

```bash
git clone https://github.com/behzadshakouri/GIStoOHQ.git
cd GIStoOHQ
python -m pip install -e .
ohqbuild doctor
```

Install the Python GIS readers when using GeoPackage and raster utilities:

```bash
python -m pip install -e '.[gis]'
ohqbuild doctor --strict-gis
```

`doctor --strict-gis` also checks QGIS Processing, the native provider, GRASS
algorithms, and the retained GIS scripts. QGIS itself must be installed through
the operating system or QGIS distribution rather than from this Python extra.

## Quick start from existing GIS outputs

The minimum modeled site contains:

```text
<SITE>/outputs/topology.gpkg
<SITE>/outputs/subwatershed_params.gpkg
<SITE>/outputs/reaches.gpkg
<SITE>/outputs/junctions.gpkg
```

Validate the files and build the OpenHydroQual alternatives:

```bash
ohqbuild check-inputs --root /path/to/project --site path/to/site
ohqbuild build --root /path/to/project --site path/to/site \
  --project-name MyWatershed --out /path/to/project/path/to/site/outputs/MyWatershed.ohq
```

Open the generated `.ohq` file in OpenHydroQual to review resources, connections,
initial conditions, forcings, and simulation settings before running it. Use
`ohqbuild build-hms` and `ohqbuild validate-hms` to generate and check the
HEC-HMS project from the same GIS topology.

## Complete outlet-to-model workflow

Run the full U.S. workflow from a QGIS-enabled Python environment:

```bash
ohqbuild full-run \
  --root /path/to/project \
  --site sites/MyWatershed \
  --lat 38.9784 --lon -76.9887 \
  --target-crs EPSG:26918 \
  --project-name MyWatershed
```

`full-run` performs source acquisition, materialization, hydrologic preparation,
the two GIS phases, input validation, OpenHydroQual writing, HEC-HMS writing, and
report generation. The same workflow is available as explicit stages:

```bash
ohqbuild download-inputs --root /path/to/project --site sites/MyWatershed \
  --lat 38.9784 --lon -76.9887 --buffer 5000
ohqbuild materialize-inputs --root /path/to/project --site sites/MyWatershed
ohqbuild prepare-inputs --root /path/to/project --site sites/MyWatershed
ohqbuild build --root /path/to/project --site sites/MyWatershed
```

Use `--reuse-downloads` (alias `--offline`) only after the source-download cache,
soil products, and Atlas 14 file are populated. Offline mode stops and reports
missing prerequisites instead of contacting remote services.

## Desktop and QGIS interfaces

Launch the desktop workflow from a graphical terminal:

```bash
scripts/run_dem_ui.sh
```

The Tk interface supports outlet selection, rectangular or polygonal acquisition
areas, OpenStreetMap/Esri/OpenTopoMap backgrounds, staged execution, reviewed
pour points, run logs, and opening generated layers in QGIS. On Debian or Ubuntu,
install `python3-tk` if `tkinter` is unavailable.

For the QGIS dock, install the development plugin and restart QGIS:

```bash
scripts/install_qgis_plugin.sh
```

See the [QGIS plugin guide](qgis_plugin/README.md) for setup and update details.

## Optional OpenHydroQual forcing and routing

The writer reads optional settings from environment variables:

| Setting | Effect |
|---|---|
| `OHQ_RAINFALL_FILE` | Assigns an OpenHydroQual precipitation time series |
| `OHQ_ET_FILE` | Assigns nonnegative ET depth rates in m/day to supported HRUs |
| `OHQ_TEMPERATURE_FILE` | Enables rain/snow partitioning, snow storage, and degree-day melt |
| `OHQ_TEMPERATURE_REFERENCE_ELEVATION_M` | Enables subbasin temperature adjustment by elevation |
| `OHQ_TEMPERATURE_LAPSE_RATE_C_PER_KM` | Overrides the default -6.5 °C/km lapse rate |
| `OHQ_BASEFLOW_METHOD=linear_reservoir` | Replaces Darcy baseflow in standard and mixed HRUs |
| `OHQ_CHANNEL_PROFILES_FILE` | Builds reviewed segmented trapezoidal channels for the mixed HRU model |

Generate candidate channel profiles from a projected, metre-based reach layer and
bare-earth DEM:

```bash
ohqbuild sample-channel-profiles \
  --reaches outputs/reaches.gpkg \
  --dem outputs/cliped_utm_wsclip.tif \
  --output outputs/channel_profiles.csv \
  --spacing-m 50 --half-width-m 30 --sample-step-m 1
```

Bank offsets are deliberately left blank for review. Standard topographic lidar
may represent the water surface rather than submerged bed geometry, so the fitted
sections remain marked `DEM_ONLY_UNVERIFIED` until checked against suitable field
or bathymetric data.

## Optional watershed observations and HydroPINN handoff

`ohqbuild data` manages a separate provider-neutral data path for USGS discharge,
NASA POWER meteorology and PET/ET, archived forecasts, temporal harmonization,
quality control, immutable source objects, portable packages, and deterministic
HydroPINN exports. It does not change watershed topology or silently assign a
downloaded variable to an OpenHydroQual forcing.

```bash
ohqbuild data --help
```

See [Optional watershed data](docs/watershed_data.md) and the
[verified HydroPINN handoff](docs/hydropinn_handoff_verified.md) for commands,
units, provenance, and quality-control behavior.

## Geographic coverage

The unattended production `full-run` currently targets U.S. federal sources.
International sites can use the implemented Copernicus DEM, OpenStreetMap vector,
SoilGrids texture, and land-cover mapping commands, followed by reviewed manual
materialization. A complete unattended international `full-run`, global HSG
derivation, and a universal Atlas 14 replacement are not implemented. See
[Building sites outside the United States](docs/international_sites.md).

## Scientific review boundaries

- WBD HUC12 polygons are authoritative reference units, not automatic model
  boundaries. Compare them with the outlet-derived basin and NHDPlus catchments.
- Reviewed pour points control subwatershed partitioning. Final incremental
  polygons must be disjoint and cover one valid root basin.
- Standard and mixed HRUs represent local land columns. At large aggregated
  subbasin scales they may absorb nearly all water before appreciable surface
  routing develops. Treat regional results as structurally limited until the
  formulation is validated for that scale.
- Snow component checks establish mass-conserving partitioning and melt behavior;
  they do not establish basin-scale runoff calibration.
- Reservoirs, dams, submerged channel geometry, and private storm-drain systems
  require reviewed geometry and outlet information; GIStoOHQ does not infer them
  from flat terrain alone.

Read [Authoritative watershed references](docs/authoritative_watersheds.md) and
[HRU scale limitations](docs/hru_scale_limitations.md) before interpreting model
results.

## Examples

- `examples/SligoCreek` demonstrates outlet and documented-watershed handling,
  acquisition-area preparation, and the U.S. workflow configuration.
- `examples/JohnMcCormack3600` is a street-scale starter whose drainage candidate
  must be reconciled with plans, utility records, or field survey.
- `examples/AZ12-100` contains small GIS fixtures for reproducible checks.

Bundled smoke fixtures are not complete production datasets. See
[Examples](docs/examples.md) and each example directory's README for scope and
required external inputs.

## Documentation

| Topic | Guide |
|---|---|
| System structure | [Architecture](docs/architecture.md) |
| Workflow stages | [Workflow](docs/workflow.md) |
| Creating required GIS inputs | [Input generation](docs/input_generation.md) |
| Source acquisition | [Data downloaders](docs/data_downloaders.md) |
| GIS-to-OHQ mapping and model options | [OHQ mapping](docs/ohq_mapping.md) |
| OHQ syntax notes | [OHQ format](docs/ohq_format.md) |
| Watershed observations and packages | [Watershed data](docs/watershed_data.md) |
| U.S. boundary validation | [Authoritative watersheds](docs/authoritative_watersheds.md) |
| International sites | [International sites](docs/international_sites.md) |
| Development | [Developer notes](docs/developer.md) |

## Testing

```bash
python -m pip install -e '.[dev,gis]'
python -m pytest -q
```

Tests that require a live QGIS/GRASS installation are skipped or isolated from
the normal Python suite. Before a production run, also execute
`ohqbuild doctor --strict-gis` in the same environment used by QGIS.

## Citation and license

If you use GIStoOHQ, cite the software using [`CITATION.cff`](CITATION.cff).
GIStoOHQ is distributed under the [MIT License](LICENSE).

For the modeling engine, cite Massoudieh, A., Nguyen, K., and Murthy, S. (2023),
“An extensible, plugin-based tool for modeling flow and reactive transport in
water systems,” *Environmental Modelling & Software*, 164, 105707,
[doi:10.1016/j.envsoft.2023.105707](https://doi.org/10.1016/j.envsoft.2023.105707).
The current process formulations are documented in Massoudieh, A. and Shakouri,
B. (2025), *OpenHydroQual: Theory and Equations Manual*. OpenHydroQual is
maintained and licensed separately in the
[OpenHydroQual source repository](https://github.com/ArashMassoudieh/OpenHydroQual).

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

### Phase 0 — prepare site inputs

Before running the QGIS workflow, prepare a site directory like:

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

### Phase 1 — create reach and junction network

Open QGIS, then run the phase-1 orchestrator in the QGIS Python Console:

```python
ROOT = "/path/to/NHA"
SITE_DIR = "WS3_GIS/AZ12-100"
SCRIPT_DIR = "/path/to/GIStoOHQ/scripts/legacy_gis"
exec(open(SCRIPT_DIR + "/run_phase1.py").read())
```

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

After phase 1, inspect `reaches.gpkg` and `junctions.gpkg` in QGIS. Then create
interior pour points manually and save them as:

```text
<ROOT>/<SITE>/outputs/pour_points.shp
```

### Phase 2 — create subbasin parameters and topology

After manually placing `pour_points.shp`, run the phase-2 orchestrator in the
QGIS Python Console:

```python
ROOT = "/path/to/NHA"
SITE_DIR = "WS3_GIS/AZ12-100"
SCRIPT_DIR = "/path/to/GIStoOHQ/scripts/legacy_gis"
exec(open(SCRIPT_DIR + "/run_phase2.py").read())
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

## Run GIStoOHQ after generating inputs

Once the four required inputs exist, build the OHQ file from a shell:

```bash
ohqbuild build --root /path/to/NHA --site WS3_GIS/AZ12-100
```

Validate without writing an OHQ file:

```bash
ohqbuild validate --root /path/to/NHA --site WS3_GIS/AZ12-100
```

## Current limitation

The QGIS scripts are legacy preprocessing tools. They are documented here as the
current way to produce GIStoOHQ inputs, but they are not yet exposed as a
cross-platform command-line pipeline. The stable package boundary is the four
GeoPackage inputs listed above.

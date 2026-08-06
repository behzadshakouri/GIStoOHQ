# 3600 John McCormack Rd NE, Washington, DC

This is a starter workflow for **3600 John McCormack Rd NE Plan Set 20240709**.
The repository does not include the plan-set documents or authoritative survey, outfall, drainage-boundary, or hydrography data. The example now uses the outlet candidate supplied in `JM.kmz`: **38.93589535566567, -76.99597205373109**. Public basemaps can locate the point, but they do not establish the private/site storm-drain connection. Consequently the repository treats it as a **local drainage candidate**, not a surveyed or record-drawing-confirmed outfall. Reconcile it with the 2024-07-09 plan-set drainage structures, DC Water records, and a field survey before final design.

The configured acquisition area uses a **500 m half-width** (a 1 km by 1 km window) around the candidate. That scale provides more local drainage context around John McCormack Road for bioretention screening; it is still not a watershed-scale delineation. The machine-readable point, extent, and limitations are recorded in `outlet_and_extent.geojson`. Useful authoritative follow-up sources are the [DC Open Data portal](https://opendata.dc.gov/) and [DC Water](https://www.dcwater.com/), because a surface-water web map alone cannot verify a buried storm-sewer outlet.

If **FULL RUN** is pressed without drawing an area, the UI first regenerates the
configured 1 km by 1 km default polygon and passes it to `full-run`. Because
the outlet is already inside that polygon, full-run preserves its clipping
bounds rather than replacing it with the former 500 m outlet safety extent. Its
default source query radius is derived from the polygon instead of reverting to
5,000 m. A user-drawn polygon (`method: polygon`) continues to take precedence.

Launch the UI from the repository root:

```bash
scripts/run_dem_ui.sh
```

Change **Config** to:

```text
examples/JohnMcCormack3600/dem_workflow.example.yaml
```

Then use **Pick outlet**, **Draw rectangle**, or **Draw polygon**. For a rectangle,
click one corner and then the opposite corner; the area is saved after the second
click. For a polygon, click at least three vertices and select **Finish area**.

This starter intentionally has no local DEM tile index. Do not use **Download DEM
Tiles** or **Materialize Inputs** before source products exist. After verifying the
outlet/area, click **RUN RECOMMENDED NEXT STEP** (which selects **FULL RUN** for a
new project) or click **FULL RUN: download all data to OHQ** directly.

For a network-enabled, end-to-end production run using the verified outlet, use:

```bash
ohqbuild full-run \
  --root examples/JohnMcCormack3600 \
  --site JohnMcCormack3600 \
  --lon -76.99597205373109 \
  --lat 38.93589535566567 \
  --target-crs EPSG:26918 \
  --acquisition-area examples/JohnMcCormack3600/intermediate/dem_acquisition_area.geojson \
  --project-name JohnMcCormack3600 \
  --out examples/JohnMcCormack3600/JohnMcCormack3600.ohq
```

`full-run` downloads source products and performs materialization, hydrology/GIS
preparation, validation, and OHQ creation. It requires internet access, GIS Python
dependencies, and QGIS processing support. Do not substitute the building centroid
for the drainage outlet without reviewing the plan set and local drainage data.

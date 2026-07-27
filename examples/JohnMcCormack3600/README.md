# 3600 John McCormack Rd NE, Washington, DC

This is a starter workflow for **3600 John McCormack Rd NE Plan Set 20240709**.
The repository does not include the plan-set documents or authoritative survey,
outfall, drainage-boundary, or hydrography data. The coordinate in the example
config is deliberately labeled as a starter location: open the map and verify the
actual receiving drainage outlet or draw the project drainage boundary before a
production run.

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

For a network-enabled, end-to-end production run using the verified outlet, use:

```bash
ohqbuild full-run \
  --root examples/JohnMcCormack3600 \
  --site JohnMcCormack3600 \
  --lon VERIFIED_OUTLET_LONGITUDE \
  --lat VERIFIED_OUTLET_LATITUDE \
  --target-crs EPSG:26918 \
  --project-name JohnMcCormack3600 \
  --out examples/JohnMcCormack3600/JohnMcCormack3600.ohq
```

`full-run` downloads source products and performs materialization, hydrology/GIS
preparation, validation, and OHQ creation. It requires internet access, GIS Python
dependencies, and QGIS processing support. Do not substitute the building centroid
for the drainage outlet without reviewing the plan set and local drainage data.

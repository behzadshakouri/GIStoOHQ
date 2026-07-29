# Authoritative U.S. watershed references

GIStoOHQ queries the standalone USGS Watershed Boundary Dataset (WBD) product for
explicit `wbd` downloads and accepts vector formats only. Production full runs
also inspect the downloaded NHDPlus HR/NHD vector package as a fallback because
some distributions carry WBDHU layers:

```bash
ohqbuild download-data sites.csv --products wbd --download source_downloads
ohqbuild download-data sites.csv --products dem,hydro --download source_downloads
```

`download-inputs` and `full-run` request hydrography once and inspect that vector
package for WBDHU before continuing without a WBD reference. During
`materialize-inputs` or `full-run`, GIStoOHQ extracts intersecting HUC12 features
to `outputs/WBDHU12_reference.gpkg` when materialization bounds are available. It
does not silently substitute that reference for the model watershed.
Standalone WBD selection rejects NHDPlus `_RASTER` archives. If neither a valid
standalone WBD vector package nor a vector hydro package containing an HU12 layer
is available, the run continues with DEM delineation and writes
`outputs/WBD_MATERIALIZATION_WARNING.txt` instead of treating WBD as mandatory.
After delineation, `full-run` writes `outputs/watershed_wbd_comparison.json` with
per-HUC12 area, intersection, omission, commission, IoU, and boundary Hausdorff
distance. Metrics use a locally estimated projected CRS and identify the highest-
IoU feature as a review candidate—not an automatically accepted boundary.

## What each national dataset can establish

| Source | Best use | Important limitation |
|---|---|---|
| WBD | Published hydrologic-unit boundaries and HUC identifiers | A HUC12 is not necessarily a named creek watershed or a modeling subbasin |
| NHDPlus HR / NHD | Channel location, connectivity, reach identifiers, and outlet snapping | Mapped channels can omit small storm-drain or ephemeral paths |
| 3DEP | Terrain-driven divides and local flow paths | Unconditioned DEMs can route across culverts, roads, and urban drainage |
| Local lidar / stormwater GIS | Fine urban terrain, pipes, culverts, and engineered drainage | Coverage and licensing vary by jurisdiction |

When present in the downloaded NHDPlus vector package, catchments are clipped to
the DEM extent and written to `outputs/NHDPlusCatchment_clip.gpkg`. This provides
the polygon/reach linkage needed for a later upstream trace from the verified
outlet while keeping NHDPlus catchments distinct from WBD hydrologic units.
When the flowlines expose `FromNode`/`ToNode` connectivity and their reach IDs
match catchment `FEATUREID`/`NHDPlusID` values, `full-run` snaps the supplied outlet
to the nearest reach, traces all upstream reaches, and writes
`outputs/NHDPlus_upstream_candidate.gpkg`. Its companion JSON records the selected
reach, snap distance, field mapping, and feature counts. Missing connectivity is
reported for review rather than guessed from line orientation.
The trace and DEM delineation reject an outlet movement greater than 50 m by
default; change `--nhdplus-snap-distance-m` only when the larger movement has been
checked against local hydrography.
The DEM script deliberately keeps a wider 150 m `OUTLET_SEARCH_RADIUS_M` for
diagnostics while enforcing the 50 m `MAX_OUTLET_SNAP_M` acceptance limit. After
editing `outputs/outlet.shp` in QGIS, rerun with `--use-existing-outlet`; full-run
will read that point, transform it to EPSG:4326 for downloads/tracing, and will not
recreate the shapefile from the old command-line longitude/latitude.
The log always states either `Outlet source: existing outputs/outlet.shp` or
`Outlet source: CLI longitude/latitude (outlet.shp will be recreated)`. The alias
`--preserve-existing-outlet` is accepted for the same explicit preservation mode.
After the DEM delineation finishes, `full-run` compares it with the NHDPlus
upstream boundary and writes `watershed_nhdplus_comparison.json`. Both WBD and
NHDPlus comparisons now include a disagreement GeoPackage with separate
`intersection`, `generated_only`, and `reference_only` layers, making roads,
culverts, confluences, and boundary errors directly inspectable in QGIS.
The final `watershed_report.html` summarizes the best WBD and NHDPlus match,
including IoU, generated-only area, reference-only area, Hausdorff distance, and
the disagreement-map path. This keeps the boundary decision beside the model
parameters rather than buried in standalone JSON files.
If the NHDPlus upstream trace succeeds, `full-run` also writes
`outputs/pour_point_candidates.gpkg`. It contains the snapped watershed outlet and
nodes receiving at least two upstream reaches, with `reason`, `n_in`, and
`review_status` attributes. This is a review layer only: Phase 2 does not consume
it until a future user-approval step promotes selected candidates.
After setting candidate `review_status` values to `approved` or `required`, run
`ohqbuild promote-pour-points --root ROOT --site SITE`. The command requires one
selected watershed outlet, unique candidate IDs, boundary containment, and 100 m
minimum spacing by default before writing `outputs/pour_points.shp`. Use
`--minimum-spacing-m` to set a justified site-specific distance.
To rebuild the complete project with those approved points, rerun `full-run` with
`--use-reviewed-pour-points`. This mode requires the promoted shapefile and turns
off automatic pour-point creation and refresh, preventing the reviewed selection
from being silently replaced before Phase 2.

WBD hierarchy is useful context, but HUC level must not be chosen merely to obtain
more polygons. HUC subdivisions are nested hydrologic units; they are not a
national set of tributary-scale catchments at an arbitrary outlet.

## Recommended U.S. workflow

1. **Confirm the target outlet.** Snap the approximate point to the intended
   NHDPlus HR flowline and visually check the confluence. Record the original and
   snapped positions and reject unexpectedly large moves.
2. **Delineate the named-stream basin.** Use the best available 3DEP or local lidar
   DEM, hydro-condition it with streams plus known culverts/storm drains, and
   delineate only the area upstream of the confirmed outlet.
3. **Use authoritative layers as constraints and checks.** Compare the result with
   WBD and NHDPlus catchments. A containing HUC12 is evidence, not ground truth for
   a smaller named creek.
4. **Create model subbasins for a stated purpose.** Place pour points at tributary
   confluences, gauges, control structures, and the watershed outlet. A flow-
   accumulation threshold alone should not decide the subbasin count.
5. **Review urban exceptions.** Roads, culverts, pipes, diversions, and storm-sewer
   outfalls can make surface topography disagree with the effective drainage area.
6. **Preserve provenance.** Store source product, vintage, resolution, processing
   CRS, outlet coordinates, snapping distance, conditioning operations, and manual
   edits with the output.

For Sligo Creek specifically, matching the published count of 14 polygons is not a
valid optimization objective by itself. Those boundaries may have been selected
for sampling or model structure. Reproduce them only when the paper or its data
defines the pour points; otherwise create defensible tributary-based catchments and
document why they differ.

## Validation gates

Report these checks rather than automatically tuning until one map resembles
another:

- outlet-to-flowline snapping distance and selected reach identifier;
- generated area versus the relevant reference drainage area;
- intersection-over-union (IoU) with an applicable reference polygon;
- omission and commission area, reported separately;
- boundary distance statistics, especially near roads and low-relief divides;
- channel/catchment consistency: each subbasin has one downstream outlet and the
  reach graph is acyclic;
- sensitivity to DEM resolution, conditioning depth, and pour-point placement.

Automatic threshold searches can overfit a reference polygon while producing a
physically poor channel network. Treat low agreement as a review flag. Never move
the outlet, change depression handling, or burn channels solely to maximize IoU
without recording the change and verifying that it is hydrologically plausible.

The raster outlet snap uses two distinct limits. It first chooses the strongest
routed cell within the maximum accepted movement (50 m by default). The wider
search radius (150 m by default) is diagnostic only when no qualifying routed cell
exists inside the accepted limit. This prevents a reviewed outlet from moving
farther downstream on each run merely because accumulation increases downstream.

If TNM returns no standalone WBD package, materialization now queries the official
WBD ArcGIS service, discovers the HUC12 layer by name, and writes the same clipped
`WBDHU12_reference.gpkg` review layer. Service failure remains non-fatal: the run
continues in DEM/NHD mode and records `WBD_MATERIALIZATION_WARNING.txt` so the
absence of an authoritative polygon cannot be mistaken for a completed comparison.

The full-run success summary prints the best matching reference identifier, both
areas, signed area difference, IoU, omission and commission areas, and boundary
Hausdorff distance. This makes a successful WBD service fallback visible in the
terminal even though the earlier TNM package query may still report zero downloads.

Full runs also compare `reaches.gpkg` with `NHDFlowline_clip.gpkg` inside the final
watershed. The report records total network lengths, the percentage of each network
within a 30 m tolerance of the other, sampled mean lateral offset, and network
Hausdorff distance. These
metrics identify channel-placement disagreement separately from polygon-boundary
disagreement; they do not automatically replace DEM-derived reaches with NHD lines.

WBD selection is outlet-aware. When the service returns several HUC12 features,
GIStoOHQ first restricts selection to polygons containing the modeled outlet and
then chooses the highest-IoU feature. A reference with less than half or more than
twice the generated basin area is labeled `regional_context_not_equivalent` rather
than presented as an equivalent named-stream watershed. It remains useful regional
context, but its low IoU is not by itself evidence that DEM delineation failed.

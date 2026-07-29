# Authoritative U.S. watershed references

GIStoOHQ can download the USGS Watershed Boundary Dataset (WBD) with the other
source inputs. Use `wbd` alone or include it in a comma-separated product list:

```bash
ohqbuild download-data sites.csv --products wbd --download source_downloads
ohqbuild download-data sites.csv --products dem,hydro,wbd --download source_downloads
```

`all`, `download-inputs`, and `full-run` also request WBD. The downloader retains
the source archive and records the selected dataset in its summary. During
`materialize-inputs` or `full-run`, GIStoOHQ extracts intersecting HUC12 features
to `outputs/WBDHU12_reference.gpkg` when materialization bounds are available. It
does not silently substitute that reference for the model watershed.
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

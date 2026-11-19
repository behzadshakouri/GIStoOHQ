# Sligo Creek gauge and model catchments (18 November 2026)

The reviewed GIStoOHQ downstream basin is not the catchment of USGS01650800.
This distinction explains the HydroPINN area/outlet mismatch without treating
a valid larger modeled basin as an erroneous gauge delineation.

## Existing GIS evidence

Read-only traversal of the saved GRASS drainage grid in
`examples/SligoCreek/SligoCreekDemo/outputs`, using the same fixed 50m maximum
snap distance and largest-accumulation rule for both points:

| Point | Snapping distance | Routed cells | Upstream area |
|---|---:|---:|---:|
| USGS01650800, (-77.0048611,38.9862222) | 41.236m | 201,579 | 16.751244km² |
| Reviewed outlet, (-76.9887378616,38.9783761807) | 39.805m | 284,781 | 23.665342km² |

Gauge-upstream cells are a complete subset of the downstream catchment.
The downstream traversal reproduces the saved reviewed boundary at IoU=1.0.
The gauge candidate differs from USGS's listed6.45mi²=16.705423km² by +0.2743%.
No outlet radius, routing parameter or area was tuned to obtain that agreement.
This is strong spatial evidence of nested catchments, not complete validation
of urban hydraulic connections or meteorological spatial representativeness.
The candidate remains explicitly unaccepted for new research training.

Source metadata-only station response was saved in the HydroPINN diagnostic:
https://waterservices.usgs.gov/nwis/site/?format=rdb&sites=01650800&siteOutput=expanded&siteStatus=all

## How the mismatch entered the handoff

1. `SC outlet.kmz` supplies the downstream reviewed outlet. The accepted example
   SiteSpecs and README describe the larger23.67km² modeled basin.
2. The watershed-data UI still supplied an older23.7546km² value; the copied
   HydroPINN package froze that explicit value and source. The UI now matches
   the committed23.67km² SiteSpecs. Existing SiteSpecs/packages are unchanged.
3. The acquisition pipeline takes `station_id` separately from the SiteSpec.
   Gauge reconnaissance primarily checks distance and record overlap;
   `topological_compatibility` remains unknown unless required by policy.
   A selected nearby gauge is therefore not proof of equivalent catchment.
4. The HydroPINN exporter copies the SiteSpec's explicit catchment area.
   It correctly does not silently substitute a station drainage area, but no
   equivalence gate established that the selected gauge measured discharge
   from the modeled downstream basin. Native conversion arithmetic alone
   could not detect this spatial mismatch.

The older `docs/hydropinn_handoff_verified.md` describes structural consumer
verification, not catchment equivalence. Its historical2024 study interval
was not opened or rerun in this audit.

There are also other geometries under similarly named directories: the old
`runs/SligoCreek/outputs` boundary measures about1,114.04km² with an outlet far
south, and the Dropbox OHQ-model copy measures29.1837km². Neither is the current
reviewed23.665342km² example boundary. They were inspected as GIS geometry only
and left intact; no substitution was made.

## Separate reproduced fallback bug

The Python fallback in `delineate_whole_watershed.py` incorrectly interpreted
code1 as east, rotating all directions45degrees. Official GRASS uses
1=NE,2=N,3=NW,4=W,5=SW,6=S,7=SE,8=E:
https://grass.osgeo.org/grass-stable/manuals/r.watershed.html

An inward-draining3×3 fixture returned one cell before correction, nine after.
The production mapping is corrected and exercised through the actual fallback
function with mocked GDAL I/O: all eight directions, chain traversal, input
immutability and outside-outlet rejection. The normal GRASS provider path is
unchanged. This bug is NOT established as the cause of the historical package
area mismatch; different selected outlets explain that independently.
No original GIS outputs or modeled watershed were regenerated/overwritten.
Full interactive QGIS processing was not rerun.

## Reproduce without observations or downloads

Run `scripts/audit_sligo_gauge_catchment.py` with the current GIS outputs,
original package SiteSpec, saved USGS metadata RDB and a NEW output directory.
Optional `--plot` saves a comparison image (requires matplotlib). The auditor
uses the actual production fallback's direction map, hashes its source inputs,
exports both candidate polygons and reports nesting, areas and boundary IoU.
It does not invoke a QGIS workflow or access time-series assets.

The exact outputs/hashes, source copies, command and test logs are saved in
`Dropbox/HydroPINN paper/audits/gistohq_sligo_gauge_alignment_20261118/accepted`.

## Next input plan

For HydroPINN discharge prediction at01650800, review/use a NEW upstream-gauge
basin and consistent area metadata, not the whole downstream-basin area.
Review the candidate against authoritative flowlines/urban drainage and ensure
rain/PET represent the upstream area. Keep station drainage area versus GIS
candidate area explicit; do not silently swap normalization conventions.
Create a separate bounded presealed input package; do not modify old data,
relabel historical scores or apply test-based rescaling. New acquisition and
research fitting require the existing review/budget gates. This audit does
not authorize either. The larger downstream basin can remain valid for its
separate OHQ modeling purpose.

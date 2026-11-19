# Automatic watershed delineation and verification

The intended workflow is automatic delineation followed by evidence-based
acceptance, with explicit review when evidence is missing or contradictory.
A delineated polygon is not automatically a verified watershed.

## Current safeguards

The full-run confidence summary distinguishes reference agreement from
independent verification. A short outlet snap or close stream network alone
cannot establish the watershed. A comparable boundary must also confirm outlet
containment. Acquisition estimates and regional HUC context do not count as
matching watershed boundaries. Invalid boundary metrics cannot earn confidence.
These checks run through the existing full-run summary; they do not change
outlet selection or make the underlying delineation correct by themselves.

## Remaining production acceptance requirements

1. Tie each run to its exact outlet, selected stream/reach, source revisions,
   DEM grid/conditioning and file hashes. Reject stale comparison reports from
   different geometries or source revisions.
2. Evaluate candidate routed cells near the outlet with explicit branch identity.
   Detect confluence ambiguity; never choose the largest accumulation merely
   because it is nearby. Record candidate areas and displacement without tuning
   selection to the desired area.
3. Verify grid alignment, valid routing, connected upstream support, edge
   truncation and area/topology consistency on the generated basin.
4. For US sites, use available authoritative hydrography and station drainage-area
   metadata as independent checks. Regional boundaries are context unless they
   describe the same outlet/domain. An internal gauge checks its upstream basin,
   not the larger full-model basin.
5. Require geometric overlap and outlet/reach agreement where an equivalent
   reference basin exists. Area agreement alone is insufficient. Source
   dependence must be disclosed; two products derived from one DEM are not two
   independent confirmations.
6. Save the acceptance decision, evidence, unresolved checks and reproducible
   scripts beside each versioned output. Missing evidence produces a review
   requirement rather than a verification claim.
7. Establish a held-out US regression set covering confluences, small urban
   basins, large rural basins, flat/coastal terrain and DEM-edge failures before
   claiming general US verification reliability. Do not hardcode Sligo's area.

## Sligo reference case

The preserved 10 m reference routing reproduces 291,837 cells, 29.1837 km²
(reported as 29.2), exactly matching its saved basin raster. The internal USGS
01650800 control drains 16.7375 km² in that topology. The earlier 23.6653 km²
GIStoOHQ basin belongs to an intermediate outlet. These domains must remain
separate.

Preserved inputs and evidence belong exclusively under
`/home/arash/Dropbox/HydroPINN paper/data/GIStoOHQ_full_watershed/`.
Do not access or modify the separate OHQ Watershed Models folder.
The production boundary script has now reproduced the reference using the
preserved conditioned routing: 291,837 cells, no differing basin cells and
boundary IoU 1.0. A repeat-snap defect was found first: selecting larger nearby
accumulation moved the already-centred outlet 41 m downstream and produced
30.6585 km². Centred qualifying channel cells now retain their routing identity.
This prevents repeat-run drift; it does not independently establish that the
chosen branch/outlet is correct. Other non-centred points still require branch
and confluence review.

Run `scripts/verify_preserved_routing_delineation.py PRESERVED_REFERENCE NEW_OUT`
with a compatible QGIS/GDAL runtime. This machine's system GDAL requires the
system NumPy (`PYTHONNOUSERSITE=1`); importing its user NumPy2 build instead fails
before delineation. No system packages were changed for the acceptance run.
Fresh DEM acquisition/conditioning, a complete full-run and general US
acceptance remain unfinished.

## Builder/plugin versus solver acceptance

The saved-GIS OHQ/HMS builders and real offscreen plugin widgets have separate
receipts. The plugin data dialog rejects a missing station and generates the
expected acquisition/resume commands; provider dispatch is captured, not run.
This does not establish interactive desktop/map-tool acceptance.

`scripts/verify_ohq_solver_smoke.py` performs bounded, shortened dry-start checks
on copies of the four generated OHQ formulations. The installed console rejects
all four with property-criteria errors (channel depth; additionally HRU initial
moisture). This rejection was subsequently isolated to the old executable/build:
a clean source-pinned console accepts the unchanged generated models, solves
the short startup cases and still rejects invalid controls. See
`docs/ohq_solver_acceptance.md` for the accepted binary/receipts and remaining
Rain-source warning. This clears the criteria blocker, not the entire scientific
solver acceptance. Do not suppress verification or tune parameters to force it.
No HEC-HMS executable was found on PATH; native project structural validation
is not HEC-HMS solver acceptance.

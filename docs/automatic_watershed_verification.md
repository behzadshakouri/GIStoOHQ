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
The reference reproduction is verified; a new end-to-end GIStoOHQ delineation
and general US acceptance suite remain unfinished.

# Building sites outside the United States

GIStoOHQ's automated pipeline (`download-data`, `download-inputs`,
`full-run`) only has downloaders for US federal data sources: USGS 3DEP
(DEM), NHDPlus HR/NHD (hydrography), Census TIGER/Line (roads), NLCD
(land cover), NOAA Atlas 14 (precipitation frequency), and SSURGO-derived
hydrologic soil groups/texture (soil). None of these have coverage outside
the United States.

This doc gives the fallback recipe for each data category: **if the US
source isn't available for your site, use this global substitute instead**,
formatted to match the exact file GIStoOHQ's legacy scripts expect. This
pattern was developed and verified end-to-end building a real international
site (Zarrineh River Basin, Iran) — see that project's `HANDOFF.md` for the
full worked example and troubleshooting log this doc is distilled from.

The general shape of the fallback, for every category below, is the same:
**fetch the global equivalent, reproject/convert it to the exact filename
and format the legacy scripts expect, and place it at that exact path** —
the legacy delineation scripts themselves mostly don't care where the file
came from, only that it exists in the right shape. Where a step is a hard
requirement gate rather than something the science actually needs (see
Land cover below), the better fix may be to skip the step, not to fake an
input for it.

## 1. DEM: USGS 3DEP → Copernicus GLO-30

**If** `ohqbuild download-data --products dem` returns nothing for your
site's coordinates (outside CONUS/Alaska/territories), **use** Copernicus
GLO-30 (30 m global DEM, public, no auth, no API key):

- Tiles live at the public AWS Open Data bucket, 1°x1° each, named
  `Copernicus_DSM_COG_10_N{lat}_00_E{lon}_00_DEM.tif` (or `S`/`W` for
  southern/western hemispheres). List/fetch with `aws s3 --no-sign-request`
  or plain HTTPS.
- Mosaic the tiles covering your buffered acquisition area with
  `gdalbuildvrt`, then `gdalwarp -t_srs EPSG:<your UTM zone>` to reproject
  and clip, writing the result to `<site>/demlr/cliped_utm.tif` — the exact
  path `run_hydrology_preprocessing()`/`prepare-hydrology` and
  `delineate_whole_watershed.py` expect.
- **Buffer generously and check for edge truncation afterward.** A DEM
  buffer that's too tight silently truncates the delineated watershed
  against the DEM boundary instead of erroring — the polygon looks
  plausible (right order of magnitude) but is wrong. Check by comparing the
  delineated watershed's bounding box against the DEM's own extent: if any
  edge matches to within a few meters, the DEM was too small. On the
  Zarrineh build, an initial ~52 km buffer to the north was too tight
  (caught a full re-run in), the fix was re-clipping the already-downloaded
  tile mosaic to a much larger extent (~110-230 km margin on every side)
  rather than a targeted expansion.
- **Verify each downloaded tile** with `gdalinfo -checksum` before
  mosaicking — a truncated/corrupted download reads as a normal-looking
  file otherwise and fails much later, confusingly, inside `gdalwarp`
  (`TIFFReadEncodedTile` errors) or silently produces bad elevations.
- **Watch for a `gdalbuildvrt <glob>.tif` footgun**: if a previously
  reprojected output (e.g. a stray `cliped_utm.tif` from an earlier attempt)
  sits in the same directory as the raw source tiles, a wildcard glob can
  pick it up as the reference CRS/extent and silently exclude all the real
  source tiles from the mosaic. Use an explicit glob that only matches the
  raw tile naming pattern (e.g. `Copernicus_DSM_COG_10_N*.tif`).

## 2. Hydrography/flowline: NHDPlus HR → OpenStreetMap

**If** `ohqbuild download-data --products hydro` returns nothing for your
site (outside the US), **use** OpenStreetMap `waterway=river`/`stream` ways
via the Overpass API:

- Query `https://overpass-api.de/api/interpreter` for the named river(s) in
  a bounding box around your basin. **Use `wget`, not `curl` or Python
  `urllib`** — on at least this network, the public Overpass endpoint
  returns HTTP 406 to `curl`/`urllib` regardless of headers, but `wget`
  against the identical URL works fine. Cause not fully diagnosed; just use
  `wget`.
- Convert the resulting GeoJSON to a GeoPackage with `ogr2ogr`, reprojected
  to your target UTM CRS, written to `<site>/outputs/NHDFlowline_clip.gpkg`
  — the exact path `extract_reaches.py` (and the `prepare-hydrology`
  precondition check) expect.
- **This does not need to be perfect.** The core delineation
  (`delineate_whole_watershed.py`) is purely DEM-derived (flow direction +
  flow accumulation) and never touches the flowline file. `extract_reaches.py`
  only falls back to it if DEM-derived stream extraction yields zero
  features. The hard requirement is narrower than it looks:
  `run_hydrology_preprocessing()` (`ohqbuild prepare-hydrology`) gates on
  the file merely *existing* before it will generate `flow_dir.tif`/
  `flow_acc.tif` — so getting real geometry here is good practice for the
  fallback path and for visual QA, not strictly load-bearing for the
  delineation math itself.

## 3. Outlet/pour-point placement: expect real offsets, snap deliberately

Not a US-specific data source, but a fallback-recipe gotcha worth calling
out on its own: gauge-station coordinates from non-US sources are often
recorded to lower precision (e.g. degrees-minutes only, no seconds) or in
locally-idiosyncratic encodings. On Zarrineh, decoded station coordinates
were off from the true channel by 450-620 m — bigger than
`delineate_whole_watershed.py`'s built-in auto-snap tolerance (150 m search
radius, 50 m max accepted move). Below that tolerance the built-in snap
would have silently locked onto the nearest *any* drainage line — including
a small, wrong, local tributary — rather than erroring, producing a
plausible-looking but incorrect result.

**Recipe:** after generating `flow_acc.tif`, manually check the raw pour
point's cell value. If it's small relative to its neighbors (i.e. sitting
on a ridge/off-channel cell rather than the mainstem), search a wider radius
for the true mainstem cell (large, contiguous high-accumulation cluster) and
pre-snap the point yourself before running Phase 1, rather than trusting the
built-in tolerance to catch a large offset.

## 4. Land cover: NLCD → usually skippable for an OHQ build; global equivalent otherwise

`load_cn_inputs.py` (Phase 2) requires `landcover/nlcd_2023_<site>.tif` to
compute SCS Curve Number. NLCD is US-only.

**Confirmed on the Zarrineh build: this step is not required to write the
`.ohq` file.** `ohq_writer.py` has documented safe defaults for every
CN/soil-texture-derived field, and the existing Sligo Creek example already
runs without real soil-texture data — the same is true for CN. The actual
gate blocking progress isn't the science, it's `input_validator.py`'s schema
check on `subwatershed_params.gpkg` (column *presence*, not values), which
is bypassable via `--no-schema`/`--skip-input-check`, or satisfiable by
writing a minimal params file with the expected columns directly, rather
than sourcing real NLCD data. **Skip `load_cn_inputs.py` (and anything
downstream that depends specifically on its CN output) for an OHQ-only
build** — SCS Curve Number is a parallel-path input to the HEC-HMS export,
not something the OpenHydroQual physically-based components consume.

This confirmation is specifically about CN. It does **not** mean land cover
is never needed — `Mixed_Hydrologic_Response_Unit`'s `impervious_fraction`
is a legitimate, separate, non-CN use of land-cover data that this
investigation didn't need to resolve (Zarrineh's Phase 2 build didn't yet
need per-sub-basin impervious fractions). If a site genuinely needs
land cover for that purpose, global substitutes with no US restriction
include:

- **ESA WorldCover** (10 m, global, free) — good default, similar
  resolution to NLCD.
- **Copernicus Global Land Cover** (100 m, global, free).

Same recipe as DEM/hydrography: fetch, reproject to your target UTM CRS,
write to the exact path/filename the consuming script expects.

## 5. Roads: Census TIGER/Line → OpenStreetMap

Not yet implemented as a Python downloader even for US sites (per
`docs/data_downloaders.md`, it only exists in the vendored C++ `TigerClient`).
If a site needs roads, OpenStreetMap `highway=*` ways via the same Overpass
recipe as hydrography (§2) is the natural global substitute — no separate
tooling exists for this yet, would need to be built following the same
pattern.

## 6. Precipitation frequency: NOAA Atlas 14 → no clean global equivalent yet

Not yet implemented as a Python downloader (vendored C++ `Atlas14Client`
only, US-only by design — Atlas 14 itself is a US NOAA product with no
international counterpart of the same form). This is the hardest gap to
close cleanly. Candidates worth evaluating if a site needs
IDF-curve-equivalent data:

- National meteorological agency IDF studies/curves for the site's country,
  if published (variable quality/availability, not a single global source).
- Deriving return-period intensities from a long global reanalysis
  precipitation record (e.g. ERA5, CHIRPS) — more effort, and reanalysis
  precipitation is a much noisier basis for extreme-value statistics than a
  dense gauge network, so treat any resulting design storms with
  appropriate caution.

No fallback for this was built or tested as part of the Zarrineh work —
flagging the gap rather than a solution.

## 7. Soil (hydrologic soil groups, texture): SSURGO → SoilGrids

`ohqbuild download-hsg`/`download-texture` are SSURGO-based (US Soil Survey),
per `docs/soil_data_retrieval.md`. Global substitute:

- **SoilGrids** (ISRIC, ~250 m, global, free) — provides sand/silt/clay
  fractions and derived hydraulic properties comparable to what
  `soil_retrieval.py` extracts from SSURGO. Not yet wired into GIStoOHQ as
  an alternate downloader; would need a new module following
  `ohqbuilder/soil_retrieval.py`'s existing shape but querying SoilGrids'
  REST API instead of SSURGO.

## Suggested next step for a real global downloader

Everything above is presented as a manual/scripted fallback recipe (fetch →
reproject → place at expected path), not new `ohqbuild` subcommands. If this
pattern gets used often enough to be worth automating, the natural shape
is a `--source us|global` flag (or auto-detection by checking whether the
site's bounding box intersects CONUS/Alaska/territories) on
`download-data`/`download-inputs`/`full-run`, dispatching to a parallel set
of downloader modules (Copernicus DEM, OSM hydrography/roads, ESA WorldCover,
SoilGrids) mirroring `dem_downloader.py`/`source_materializer.py`'s existing
structure. Nobody has built this yet — the Zarrineh site's inputs were
prepared by the manual recipe above, run once for that one site.

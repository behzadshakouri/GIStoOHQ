# Building sites outside the United States

GIStoOHQ's automated pipeline (`download-data`, `download-inputs`,
`full-run`) only has downloaders for US federal data sources: USGS 3DEP
(DEM), NHDPlus HR/NHD (hydrography), Census TIGER/Line (roads), NLCD
(land cover), NOAA Atlas 14 (precipitation frequency), and SSURGO-derived
hydrologic soil groups/texture (soil). None of these have coverage outside
the United States.

This doc records candidate substitutes when a US source is unavailable. The
manual DEM and hydrography steps were exercised for the Zarrineh River Basin,
Iran. **An unattended international `full-run` is not implemented or
verified.** Other inputs need source-specific conversion and validation. See
that project's `HANDOFF.md` for the manual processing log.

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
site's coordinates, consider Copernicus GLO-30 Public. Its public tile list
has coverage gaps; it is a digital **surface** model, including buildings
and vegetation, which can matter for flow routing.

```bash
ohqbuild download-copernicus-dem \
  --bounds WEST SOUTH EAST NORTH \
  --output-dir /path/to/site/source_downloads/site/demlr
```

The command checks the published tile index, downloads only intersecting
1-degree tiles (maximum 16 unless explicitly raised), reads every raster
block to detect corruption, and records tile URLs and SHA-256 checksums in
`copernicus_dem_source.json`. Install the GIS dependencies for raster
verification. Then run `materialize-inputs` against that source directory to
mosaic, reproject, and clip; check the resulting DEM extent before delineation.

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
- **Verify each downloaded tile** before mosaicking. The command reads
  every raster block; manually downloaded tiles need the same check.
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

- Query an Overpass instance for river/stream ways in a reviewed bounding box.
  The optional `download-osm-vectors` command uses a bounded POST request and
  writes line geometry as WGS84 GeoJSON. Availability depends on the public
  Overpass service; an HTTP 406 observed on one network is not a general rule
  about `curl` or Python clients.
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

For a small reviewed area, the repository can now fetch source flowlines with:

```bash
ohqbuild download-osm-vectors --product hydro \
  --bounds WEST SOUTH EAST NORTH \
  --output /path/to/site/source_downloads/site/hydro/OSMFlowline.geojson
```

`materialize-inputs` can read this flowline GeoJSON in the `hydro` source
directory and clip/project it to the DEM. OSM waterways are volunteered map
lines; they provide neither NHDPlus topology nor an independent delineated
catchment. Review completeness, licensing/attribution, and alignment locally.

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

## 4. Land cover: NLCD → globally sourced, class-mapped land cover

`load_cn_inputs.py` (Phase 2) requires `landcover/nlcd_2023_<site>.tif` to
compute SCS Curve Number. NLCD is US-only.

The standard `full-run` invokes all legacy Phase 2 steps, including
`load_cn_inputs.py` and CN raster preparation. It has no supported `--skip-cn`
mode. Writer defaults for some missing parameters do not establish that
skipping land cover produces an equivalent model. Do not bypass schema
validation or create dummy land-cover/soil rasters for a production run.

This confirmation is specifically about CN. It does **not** mean land cover
is never needed — `Mixed_Hydrologic_Response_Unit`'s `impervious_fraction`
is a legitimate, separate, non-CN use of land-cover data that this
investigation didn't need to resolve (Zarrineh's Phase 2 build didn't yet
need per-sub-basin impervious fractions). If a site genuinely needs
land cover for that purpose, global substitutes with no US restriction
include:

- **ESA WorldCover** (10 m, global, free) — candidate global source.
- **Copernicus Global Land Cover** (100 m, global, free).

These products use class codes different from NLCD. Reprojection and renaming
alone would feed wrong classes into `cn_lookup.csv` and impervious-area
calculations. A reviewed class crosswalk and appropriate hydrologic soil groups
are required before replacing NLCD in the CN path.

`ohqbuild map-landcover --input SOURCE.tif --crosswalk REVIEWED.json --output
MAPPED.tif` now applies an explicit categorical crosswalk. The JSON must
declare `source_dataset`, `source_year`, `source_url`, `method`, and `classes`
(an object mapping each source class code to an NLCD code in `cn_lookup.csv`).
Every observed valid source class must be mapped; unknown classes stop the
conversion, while source nodata remains nodata. A `.provenance.json` sidecar
records file hashes, class counts, and the mapping. **No WorldCover-to-NLCD
mapping is bundled:** its CN and impervious-fraction interpretation requires
local hydrologic review. Copying an unreviewed mapped raster to the legacy
`nlcd_2023_<site>.tif` path would also misstate the source vintage; the
provenance sidecar must accompany any approved compatibility use.

## 5. Roads: Census TIGER/Line → OpenStreetMap

The Python `download-data` path already supports US Census TIGER/Line roads.
`download-osm-vectors --product roads --bounds WEST SOUTH EAST NORTH --output
/path/to/roads.geojson` now retrieves bounded OSM highway ways. This creates
a reviewable source vector, **not** a replacement for every TIGER consumer;
road materialization and downstream schema integration still need a site test.

## 6. Precipitation frequency: NOAA Atlas 14 → local IDF or OHQ-only mode

The Python `download-data` path already supports US NOAA Atlas 14. The
international choice depends on whether design-storm HEC-HMS outputs are
needed. If they are not, an offline run can explicitly omit them:

```bash
ohqbuild full-run --root ROOT --site SITE --lon LON --lat LAT \
  --reuse-downloads --skip-design-storms
```

This still runs all Phase 1 and Phase 2 watershed/CN steps and validates the
OHQ inputs, but omits `write_met.py`, `write_hms_project.py`, and the later
HEC-HMS fallback. It does not need `atlas14_pf.csv`, and reports the omitted
product. Source DEM/hydrography and soil/land-cover inputs still have to be
prepared and reviewed. This is an **OHQ-only model run**, not an HEC-HMS
design-storm run.

If design storms are needed, candidate sources include:

- National meteorological agency IDF studies/curves for the site's country,
  if published (variable quality/availability, not a single global source).
- [GSDR-IDF (Green et al., 2026)](https://doi.org/10.1038/s41597-026-06858-4)
  is an open gauge-based global research dataset with 1-, 3-, 6-, and 24-hour
  estimates at 10-, 30-, and 100-year return periods. Station availability
  and representativeness vary; the authors caution against extrapolation
  beyond the 1–24-hour fit range and against distant station transfers.
- Deriving return-period intensities from a long global reanalysis
  precipitation record (e.g. ERA5, CHIRPS) — more effort, and reanalysis
  precipitation is a much noisier basis for extreme-value statistics than a
  dense gauge network, so treat any resulting design storms with
  appropriate caution.

Do not relabel an international estimate as Atlas 14. The existing
`write_met.py` also constructs an SCS Type II storm from a table named
`atlas14_pf.csv`, which is not automatically appropriate outside its design
context. A sourced IDF adapter and an explicitly selected local temporal
pattern need separate implementation and validation before a global
HEC-HMS design-storm run can be supported. The OHQ-only mode above avoids
silently generating that storm.

## 7. Soil (hydrologic soil groups, texture): SSURGO → SoilGrids

`ohqbuild download-hsg`/`download-texture` are SSURGO-based (US Soil Survey),
per `docs/soil_data_retrieval.md`. Global substitute:

- **SoilGrids** (ISRIC, ~250 m, global) — a candidate source for sand, silt,
  and clay fractions. It does not directly supply US hydrologic soil group
  classes. A documented derivation and validation are needed for `hsg.tif`,
  along with compatible texture rasters and vector schemas. ISRIC currently
  reports that its SoilGrids REST API is paused. Its [WCS documentation](https://docs.isric.org/globaldata/soilgrids/wcs.html)
  describes a separate route for spatial subsets.

`ohqbuild download-soilgrids-texture --bounds-projected XMIN YMIN XMAX YMAX
--depth 0-5cm --output-dir DIR` fetches the raw Q0.5 sand, silt and clay
coverages through WCS and records URLs and checksums. The bounds must be in
the service's **native EPSG:152160 coordinates**; longitude/latitude will
produce a wrong or empty subset. SoilGrids stores these texture properties
as g/kg: divide by 10 for percent. The command intentionally does **not**
write `hsg.tif`, `soil_texture.gpkg`, or the legacy `sand_pct.tif` files.
Those require a documented depth aggregation, grid conversion, and an
independent HSG method before they can be used in Phase 2. Check the source
extent against the modeled watershed and expect WCS service outages.

## Suggested next step for a real global downloader

Everything above is presented as a manual/scripted fallback recipe (fetch →
reproject → place at expected path), not new `ohqbuild` subcommands. If this
pattern gets used often enough to be worth automating, the natural shape
is a `--source us|global` flag (or auto-detection by checking whether the
site's bounding box intersects CONUS/Alaska/territories) on
`download-data`/`download-inputs`/`full-run`, dispatching to a parallel set
of downloader modules (Copernicus DEM, OSM hydrography/roads, ESA WorldCover,
SoilGrids) mirroring `dem_downloader.py`/`source_materializer.py`'s existing
structure. The Copernicus and OSM commands are source-acquisition building
blocks, not an
automatic global `full-run`. The Zarrineh inputs were prepared manually for
that site. Define CN/soil and design-storm semantics before automating the
remaining products.

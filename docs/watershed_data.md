# Optional watershed data

The watershed-data commands are an additive foundation for discharge, weather,
PET/ET, forecasts, and future provider products. They are not required by
`ohqbuild full-run` and currently do not perform automatic provider discovery.

## Create and validate a site specification

```bash
ohqbuild data init-site \
  --site-spec sites/hickey_run.yaml \
  --site-id hickey_run --name "Hickey Run" \
  --lon -76.98 --lat 38.92 \
  --start 2018-01-01T00:00:00Z \
  --end 2025-12-31T23:00:00Z

ohqbuild data validate-site --site-spec sites/hickey_run.yaml
```

The generated source entries are policies, not provider selections. Automatic
reconnaissance will be added as a later provider adapter; it must record all
candidates, constraint results, scores, rejection reasons, and its decision.

## Discover discharge gauges before downloading

```bash
ohqbuild data reconnaissance --site-spec sites/hickey_run.yaml \
  --output reconnaissance --radius-km 50
```

The command queries the USGS site service for stations that publish discharge,
then writes `report.json` and `report.md`. It records every candidate, distance,
record overlap, constraint result, score, rejection reason, and selection
decision. A required topology check is never guessed: candidates remain
unacceptable until a later spatial-topology adapter can establish compatibility.

## Download native observed discharge

After reviewing reconnaissance, record or enter an explicit gauge ID and run:

```bash
ohqbuild data download-discharge --site-spec sites/hickey_run.yaml \
  --station-id 01649500 --cache .gistoohq-cache \
  --catalog watershed_package/catalog.json
```

GIStoOHQ stores the exact USGS WaterML JSON response in the immutable object
store. Its catalog record preserves the station and parameter identifiers,
native units, native timezone offsets, temporal coverage, observation count,
provider qualifiers, missing-value sentinel, request parameters, request key,
and content digest. It does not aggregate, interpolate, normalize, or convert
units during native acquisition.

## Acquire an explicitly declared product

The first generic acquisition primitive stores any explicit HTTPS product once
by its raw SHA-256 digest and registers it in an asset catalog:

```bash
ohqbuild data acquire-url \
  --url https://provider.example/data.csv \
  --provider example --product hourly-weather --product-version 2026 \
  --cache .gistoohq-cache --catalog watershed_package/catalog.json
```

This command is useful to adapter developers and advanced users. A provider
adapter—not the UI—will eventually construct these requests for weather,
discharge, PET/ET, and forecast selections.

## Freeze and validate a package

```bash
ohqbuild data freeze --site-spec sites/hickey_run.yaml \
  --catalog watershed_package/catalog.json --output watershed_package \
  --include-raw referenced
ohqbuild data validate-package --package watershed_package
```

Use `--include-raw none` for metadata-only publication, `referenced` for a
package that depends on the local object store, or `all --object-store CACHE`
for a self-contained package. `--redistributable` is never inferred; users must
set it only after checking every provider license.

## QGIS user interface

The QGIS dock exposes an optional **Data** tab. **Open Watershed Data…** can
create or validate a SiteSpec and download an explicitly declared HTTPS product
into the immutable cache and catalog. These controls call the same `ohqbuild
data` backend used by the terminal; provider logic does not live in the UI.

Later provider adapters will extend this workflow to:

1. run reconnaissance;
2. review ambiguous source candidates;
3. select discharge, meteorology, PET/ET, and forecast products;
4. download and inspect QC;
5. freeze a generic package;
6. export to HydroPINN or another consumer.

The existing **Full Run** button remains the default route to OHQ. A future
unchecked **Also acquire watershed observations** option may orchestrate both
workflows, but temporal data must not become a prerequisite for an OHQ run.

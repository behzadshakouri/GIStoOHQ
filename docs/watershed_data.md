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

Both graphical front ends now expose this workflow:

- run `ohqbuild ui` and choose **Watershed data…** in the standalone launcher;
- open the QGIS plugin's **Data** tab and choose **Open Watershed Data…**.

In either interface, run **Discover Gauges**, review `report.md`, enter the
chosen station ID, and select **Download Selected Discharge**. These buttons run
the same backend commands documented above and do not modify Full Run to OHQ.

## Download native historical weather

```bash
ohqbuild data download-weather --site-spec sites/hickey_run.yaml \
  --cache .gistoohq-cache --catalog watershed_package/catalog.json \
  --variables PRECTOTCORR,T2M,RH2M,WS2M,ALLSKY_SFC_SW_DWN
```

The NASA POWER adapter downloads hourly point precipitation, temperature,
humidity, wind, and surface solar radiation in UTC. The raw response, native
variable names and units, point coordinates, coverage, counts, and missing-value
counts are cataloged without resampling or filling gaps. Both graphical data
dialogs expose this as **Download Weather**.

## Harmonize and run generic temporal QC

Native assets remain immutable. To create a separate sorted UTC table while
preserving native units, missing values, and provider qualifiers:

```bash
ohqbuild data harmonize --asset-id sha256:... \
  --catalog watershed_package/catalog.json --object-store .gistoohq-cache \
  --qc-output watershed_package/quality_control/temporal.json \
  --provenance-output watershed_package/provenance/temporal.json
```

The derived CSV is a new catalog asset linked to its native parent. The QC report
uses stable rules for duplicate timestamp-variable records, missing values, and
chronology. The provenance document records transformation name/version,
parameters, software version, timestamps, parent asset, and output asset. This
step converts timestamps to UTC and sorts records; it does not aggregate,
interpolate, normalize, or convert units.

## PET/ET and HydroPINN export

Download the provider's native evapotranspiration parameter separately so its
semantics cannot be confused with observed ET or locally calculated reference ET:

```bash
ohqbuild data download-pet --site-spec sites/hickey_run.yaml \
  --cache .gistoohq-cache --catalog watershed_package/catalog.json
```

After harmonizing desired assets and freezing the generic package, create the
thin consumer export:

```bash
ohqbuild data export-hydropinn --package watershed_package \
  --object-store .gistoohq-cache --output outputs/hydropinn
```

The export contains a manifest, `variables.json`, and named observation tables.
It records the source package and checksums. It deliberately performs no
normalization, imputation, feature selection, lag construction, or experimental
partitioning. **Download PET/ET**, **Freeze Package**, **Validate Package**, and
**Export HydroPINN** are available in both graphical data dialogs.

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

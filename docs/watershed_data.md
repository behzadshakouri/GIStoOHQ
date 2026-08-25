# Optional watershed data

The watershed-data commands are an additive workflow for discharge, weather,
PET/ET, normalized forecast archives, and future provider products. They are not
required by `ohqbuild full-run`. Start with the ready-to-copy configuration in
`examples/watershed_data/site.example.yaml`.

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

The generated source entries are policies, not provider selections. USGS gauge
reconnaissance records all candidates, constraint results, scores, rejection
reasons, and its decision before discharge acquisition.

The graphical dialogs no longer assume that `sites/watershed.yaml` already
exists. They derive an absolute SiteSpec path, output workspace, cache, catalog,
and package paths from the loaded project configuration. Outlet and site values
are copied from that configuration. Add an optional study period to the project
configuration so the dialog can prefill its remaining required fields:

```yaml
watershed_data:
  study_period:
    start: 2024-01-01T00:00:00Z
    end: 2024-12-31T23:00:00Z
```

Review the values and click **Create SiteSpec** before any download action.
The two full-data buttons also pass `--init-if-missing`, so they create the
SiteSpec automatically when all site, outlet, and study-period fields are filled.

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
chosen station ID, or use **Use Reconnaissance Selection** when the report contains
one unambiguous acceptable candidate, and select **Download Selected Discharge**.
Ambiguous or rejected results are never copied automatically. These buttons run
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
chronology. It also applies declared physical bounds to known discharge, rainfall,
humidity, wind, radiation, temperature, and evapotranspiration variables; unknown
variables remain unchanged and are not assigned guessed limits.
For assets declaring hourly or daily native support, QC also identifies missing
internal intervals independently for each variable. It does not fill those gaps.
Known USGS and NASA POWER variables are checked against their declared native-unit
contracts. Unknown variables are listed as not evaluated rather than assigned a
guessed unit or converted silently.
USGS approval qualifiers are also retained and summarized. Records carrying `P`
produce a warning-level provisional-data result, while `A` is reported as approved;
the observations themselves remain unchanged.
The provenance document records transformation name/version, parameters, software
version, timestamps, parent asset, and output asset. This
step converts timestamps to UTC and sorts records; it does not aggregate,
interpolate, normalize, or convert units.
Every derived catalog asset must declare non-empty parent asset IDs plus its
transformation name, version, and parameters. Catalog publication rejects
incomplete lineage instead of relying only on optional sidecar files.

When a package is frozen, all `quality_control/*.json` reports are validated and
aggregated into the manifest's `package_qc_status`: failed error rules produce
`fail`, failed warning rules produce `warning`, otherwise executed QC produces
`pass`. A package without QC reports remains explicitly `not_run`.

## PET/ET and HydroPINN export

Download the provider's native evapotranspiration parameter separately so its
semantics cannot be confused with observed ET or locally calculated reference ET:

```bash
ohqbuild data download-pet --site-spec sites/hickey_run.yaml \
  --cache .gistoohq-cache --catalog watershed_package/catalog.json
```

The NASA POWER weather acquisition retains hourly support. The provider's
`EVPTRNS` product is requested from the daily point endpoint and remains daily;
harmonization does not silently upsample it to the target model timestep.

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

## Run the complete optional data workflow

After reconnaissance and explicit gauge selection, the CLI and both graphical
dialogs can download discharge, weather, and PET/ET; harmonize and QC each native
asset; freeze and validate the generic package; and optionally export HydroPINN:

```bash
ohqbuild data run --site-spec sites/hickey_run.yaml \
  --station-id 01649500 --workspace outputs/hickey_run_data \
  --export-hydropinn
```

Use `--no-discharge`, `--no-weather`, or `--no-pet` to omit a product. The
**RUN ALL DATA STEPS** button performs this orchestration in both graphical
interfaces. Gauge selection remains explicit; this command never silently picks
an ambiguous station. This optional pipeline remains separate from Full Run to OHQ.

An explicitly configured forecast archive can be included in the same atomic run.
When `--prediction-time` is present, the package also includes a leakage-safe
forecast view containing only forecasts issued by that time:

```bash
ohqbuild data run --site-spec sites/hickey_run.yaml \
  --station-id 01649500 --workspace outputs/hickey_run_data \
  --forecast-url https://provider.example/archive.json \
  --forecast-provider example --forecast-product precipitation \
  --prediction-time 2025-01-01T03:00:00Z --export-hydropinn
```

The same forecast fields are honored by both graphical one-button run actions.
Leaving them blank omits forecasts; supplying only a URL or only a provider is
rejected before acquisition.

For a weather/PET-only workflow that needs no gauge reconnaissance or station ID,
use **RUN WEATHER/PET TO EXPORT** in either graphical interface, or run:

```bash
ohqbuild data run --site-spec sites/hickey_run.yaml \
  --workspace outputs/hickey_run_weather --no-discharge --export-hydropinn
```

## Archived forecasts

Forecast archives must be JSON records containing `issue_time`, `valid_time`,
`lead_time_hours`, `member`, `variable`, `location_or_grid_id`, `value`, and
`units`. Acquire a provider archive and create a prediction-time view with:

```bash
ohqbuild data download-forecast --url https://provider/archive.json \
  --provider provider --product forecast --cache .gistoohq-cache \
  --catalog watershed_package/catalog.json
ohqbuild data forecast-view --asset-id sha256:... \
  --prediction-time 2025-01-01T03:00:00Z --object-store .gistoohq-cache \
  --catalog watershed_package/catalog.json
```

The view enforces `issue_time <= prediction_time`, preserves issue and valid
times, and rejects inconsistent lead times. It never collapses forecasts to valid
time alone.
Forecast validation also rejects duplicate issue/valid/member/variable/location
keys, empty dimensions or units, nonnumeric values, and NaN or infinite numbers
before either native acquisition or prediction-time view publication.
Prediction-time materialization also refuses an empty view when the requested
cutoff precedes every forecast issue time, rather than publishing a zero-record
derived asset that cannot drive a model.
Derived forecast views normalize issue and valid timestamps to UTC and strip
surrounding whitespace from dimension and unit labels; the transformation metadata
records both operations.
Each forecast variable must use one consistent normalized unit throughout an
archive. The catalog summary records that variable-to-unit mapping for downstream
profile checks.
The strengthened archive contract uses product version `forecast-records-v2`, so
responses cached under the earlier validation contract are not silently reused.
Native catalog records also carry the `forecast-validation-v1` policy version and
the SHA-256 digest of its complete required-field, numeric, timestamp, duplicate,
dimension, lead-time, and unit rules. Request identity includes that digest, and
derived forecast view 1.3 records the same fingerprint in transformation metadata,
so a policy-content change cannot reuse a cached archive or derived view silently.
Derived view catalog records repeat the filtered record count, variables, members,
locations, units, and issue/valid coverage, so consumers need not inspect the CSV
to determine whether a prediction-time view fits their profile.
Per-variable record counts, member sets, and location sets preserve the relationship
between forecast dimensions instead of exposing only archive-wide unions.
View materialization revalidates the stored native document and reports malformed
UTF-8 JSON or a non-array top level as watershed-data errors before filtering.
Provider-specific extension fields remain valid native metadata and are ignored by
the generic derived CSV writer. Provider and product identities are validated
before any network request.
Forecast dimensions and units must be non-empty JSON strings; nulls and other types
are not stringified implicitly. Lead times and values must be JSON numbers; numeric
strings and booleans are rejected instead of being coerced.
Derived rows are sorted by every forecast key dimension, making view content
digests independent of provider record order.
Lead times and values are normalized to floating-point CSV text, so equivalent JSON
integer and floating-point representations produce the same derived content. This
normalization is recorded in version 1.2 transformation metadata.

## Inspect downloaded and derived assets

```bash
ohqbuild data status --catalog watershed_package/catalog.json \
  --object-store .gistoohq-cache --output watershed_package/status
```

This writes `status.json` and `status.md` with every asset ID, provider, product,
native/derived status, parent IDs, coverage, record counts, and object-store
availability. Use **Inspect Data Status** in either graphical dialog to obtain the
same report instead of copying opaque IDs directly from console output.
Forecast entries in `status.json` include issue/valid coverage, prediction time,
variables, and the per-variable member, location, unit, and record-count summaries
carried by the catalog.
When forecasts are present, `status.md` adds a forecast-support table with issue and
valid coverage, prediction-time cutoffs, and variables for quick human review.

Before a long run or export, validate the local workspace without contacting a
provider:

```bash
ohqbuild data doctor --site-spec sites/hickey_run.yaml \
  --catalog watershed_package/catalog.json --object-store .gistoohq-cache \
  --package watershed_package
```

The doctor checks SiteSpec validity, catalog readability, every cataloged object
digest, and the optional frozen package. A structurally valid package still fails
the doctor when its aggregated QC status is `fail`; warning and `not_run` states
remain visible but do not masquerade as structural errors. **Check Data Workspace** exposes the same
operation in both graphical dialogs.

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

The object store verifies an already-present object before deduplicating a new
response. If bytes at a digest path have been corrupted, acquisition stops rather
than silently replacing an object that was previously published as immutable.
Catalog registration also rejects malformed digests, negative sizes, empty
provider/product names, and invalid media types before publishing the catalog.
Catalog locks record their owning process; a lock abandoned by a terminated local
process is reclaimed safely, while a live owner's lock is never removed.
Every catalog read recomputes the digest over its asset array and rejects modified
metadata, so package validation and cached-request reuse cannot trust a catalog
whose contents were changed outside the atomic catalog writer.

Acquisition commands reuse the newest locally available asset with the same
canonical request key, avoiding unnecessary provider calls during repeated UI or
pipeline runs. Pass `--refresh`—or select **Refresh provider responses** in either
graphical dialog—to contact the provider deliberately and retain any new response
as another immutable revision.

When a provider call is necessary, GIStoOHQ retries a complete response up to
three times with bounded exponential backoff. Only a fully read response is
published to the immutable store, so a failed or truncated attempt cannot create
a catalog asset. Successful native catalog records include `acquisition_attempts`,
and the status report exposes that count for diagnosing unstable providers.

Cache cleanup is deliberately dry-run by default. Supply every catalog that must
retain its objects, review the JSON report, and only then repeat with `--delete`:

```bash
ohqbuild data gc --object-store .gistoohq-cache \
  --catalog watershed_package/catalog.json --output cache-gc.json
ohqbuild data gc --object-store .gistoohq-cache \
  --catalog watershed_package/catalog.json --output cache-gc.json --delete
```

Objects referenced by any supplied catalog are never candidates. Destructive
cleanup is therefore explicit and auditable rather than automatic.

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
QC and provenance JSON sidecars are listed in the package manifest with SHA-256
checksums and contribute to package identity. Validation rejects missing or changed
sidecars, including edits made after a package was frozen.
Temporal QC also compares each variable's first and last non-missing observation
with the SiteSpec study period during a one-button pipeline run. An incomplete
leading or trailing window is reported separately from gaps inside a
fixed-resolution series; daily products receive one daily interval of end-boundary
tolerance. Fixed-resolution products are also checked for timestamps that fall
off their declared hourly or daily UTC grid. Missing-value QC includes per-variable
record, valid, and missing counts and a bounded sample of affected timestamps.
Duplicate timestamp-variable records likewise include bounded examples for direct
diagnosis without making large QC reports grow without limit. Chronology is
evaluated within each variable, so provider grouping of otherwise ordered series
does not produce a false warning; actual inversions include bounded examples.
Internal gap QC reports missing interval totals by variable as well as bounded gap
examples, making a multi-variable product's incomplete series directly identifiable.
A variable containing no valid observations is an error rather than ordinary
missingness, preventing an entirely empty forcing or target series from passing as
a warning-only package.
NaN and infinite numeric observations are rejected explicitly instead of escaping
ordinary physical-range comparisons or being written as usable model inputs.
Temporal QC reports declare policy version `temporal-qc-v2`. Published harmonized
assets record that policy in transformation metadata, so future rule, severity,
range, unit, interval, tolerance, or example-limit changes cannot silently reuse an
asset admitted under an older policy. Reports and derived assets also record the
SHA-256 digest of the complete canonical policy document, distinguishing exact
policy content even if a version label is accidentally reused. QC results obtain
their severities directly from that policy document, preventing report behavior
from drifting away from the fingerprinted severity map.
Package freezing treats policy metadata as an all-or-nothing pair and validates the
digest as lowercase SHA-256, preventing incomplete policy claims in QC sidecars.
Package manifests and one-button results aggregate policy versions to their exact
digests. Reusing one version label with conflicting digests is rejected.
New packages use PackageManifest 1.1 for QC rule and policy summaries. Validation
accepts legacy 1.0 manifests, recomputes the rule and policy summaries that were not
stored by that contract, and rejects unknown schema names or versions. Validation
also requires the checksummed sidecar inventory to exactly match every JSON report
under `quality_control/` and `provenance/`; undeclared additions are rejected. Those
trees cannot contain symbolic links, so a frozen package cannot validate sidecar
content located outside its own directory. Link and inventory checks run before QC
reports are parsed, ensuring validation never reads an external report first.
HydroPINN export refuses a package whose aggregated QC status is `fail`. Warning,
passing, and `not_run` packages remain exportable; callers receive the aggregate
status in one-button pipeline results and can apply stricter policy if required.
Pass `--require-qc-pass` to either `data export-hydropinn` or an exporting `data run`
to reject both warning and `not_run` packages as well. A pipeline run rejects this
option unless `--export-hydropinn` is also selected, rather than silently ignoring
the requested gate. HydroPINNExport 1.1 manifests record the source package QC
status, failed rule IDs, policy digests, and the applied `reject_fail` or
`require_pass` gate, making the export decision auditable after publication.
Package manifests and one-button results list the stable IDs of every failed QC
rule. Package validation recomputes both the aggregate status and this rule list
from the checksummed sidecars, rejecting a stale or edited summary.
Package freezing validates every QC result, including successful results, against
the QCReport 1.0 contract and requires stable dotted rule identifiers, a non-empty
message, an asset-ID array, and an object-valued details payload.
Every asset ID named by a QC result must exist in the frozen package catalog;
package-level rules may use an empty asset-ID array.
QC aggregation recursively includes JSON reports under `quality_control/`, allowing
provider or asset subdirectories without omitting their failures from the manifest.
Workspace doctor and refused HydroPINN export messages include failed rule IDs, so
users can move directly from a gate failure to the relevant QC sidecar result.
One-button pipeline harmonization also stops before publishing a derived asset when
an error-level temporal rule fails. Its QC sidecar remains available for diagnosis,
but no provenance activity or ordinary derived catalog record is created.

## Graphical interfaces

The QGIS dock exposes **Data** → **Open Watershed Data…**. The standalone launcher
exposes **Watershed data…**. Both dialogs are scrollable, group actions in a
three-column grid, and call the same `ohqbuild data` backend used by the terminal.
They support SiteSpec creation/validation, reconnaissance, discharge, weather,
PET/ET, normalized forecast archives, QC/harmonization, status reporting,
package freeze/validation, HydroPINN export, and the post-reconnaissance
**RUN ALL DATA STEPS** action.

The existing **Full Run** button remains the default route to OHQ. Watershed
observations remain optional and are never a prerequisite for an OHQ run.

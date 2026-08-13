# Architecture

The repository separates model construction into four layers:

1. Readers
2. Internal watershed objects
3. Validators
4. OHQ writers

Only `ohqbuilder/writers/` should need changes when the exact OHQ grammar is refined.

## Optional watershed data

GIStoOHQ's primary workflow remains the complete GIS-to-OHQ application. Optional
observations use a separate provider-neutral path:

```text
provider APIs -> immutable object store -> asset catalog -> generic package -> adapters
```

The watershed-data layer owns acquisition, native data, provenance, generic QC,
and declared derivations. Consumer adapters may map variables and units, but do
not normalize data, choose features or experimental partitions, construct lags,
silently interpolate scientifically important gaps, or alter watershed topology.
HydroPINN is one optional consumer; it is not a dependency of acquisition or of
the existing OHQ builder.

The existing `download-inputs`, `materialize-inputs`, `prepare-inputs`, `build`,
and `full-run` commands do not require a SiteSpec or generic package. Temporal
data acquisition is opt-in through `ohqbuild data`. This compatibility boundary
must be covered by full-run regression tests as the generic layer evolves.

### Data identity decisions

Logical request identity and returned content identity are intentionally distinct:

```text
request_key = sha256(provider, method, endpoint, canonical parameters, product version)
content_digest = sha256(raw response bytes)
```

Repeated retrievals of one request may therefore retain multiple provider
revisions. Native objects are immutable. Every future transformation must publish
a new asset and record its parent assets, operation and version, parameters,
software version, timestamps, and outputs.

The local object store is operational storage. A portable package is a frozen
release that declares whether raw objects are included, externally referenced,
redistributable, and available without the local store.

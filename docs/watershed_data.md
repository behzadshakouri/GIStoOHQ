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

## Planned user interface

The QGIS interface will expose an optional **Data** workflow containing:

1. create/load SiteSpec;
2. run reconnaissance;
3. review ambiguous source candidates;
4. select discharge, meteorology, PET/ET, and forecast products;
5. download and inspect QC;
6. freeze a generic package;
7. export to HydroPINN or another consumer.

The existing **Full Run** button remains the default route to OHQ. A future
unchecked **Also acquire watershed observations** option may orchestrate both
workflows, but temporal data must not become a prerequisite for an OHQ run.

# Gauge-basin forcing to HydroPINN

GIStoOHQ owns source acquisition, exact polygon intersection weights, source
units/time support, immutable raw responses and missing-data flags. HydroPINN
owns native discharge conversion, causal feature availability, learning,
checkpoint reload and scientific comparison. Neither stage grants a training
budget or proves urban drainage equivalence from a topographic polygon.

## Producer command

Install GIS extras (`pip install -e '.[gis]'`) and the system `zstd` utility.
Use a gauge-upstream polygon and a SiteSpec with its explicit area, outlet and
bounded study period (the study end names the last hourly interval start).

```
ohqbuild data basin-forcing --site-spec gauge_site_spec.yaml \
  --basin gauge_basin.geojson --output NEW_FORCING_DIRECTORY \
  --max-download-mb 512
```

This opt-in command acquires AORC v1.1 hour-ending precipitation accumulations
and daily gridMET short-grass reference evapotranspiration. Polygon weights
are calculated in EPSG:5070; every weighted cell must be valid. Missing cells
invalidate the interval, never become zero rain or trigger silent spatial
renormalization. Hour-ending rain is mapped to interval start. Native packed
units/scales and grid dimensions are checked. Source changes outside the
supported encoding stop processing rather than being guessed.

The weather request may read the next year's first AORC chunk to obtain the
last requested hour-ending accumulation. No discharge is downloaded. Raw
chunks/metadata are retained with hashes, URLs and a total byte limit. Resume
requires identical input hashes (`--resume`); a completed package is checked
and reused without overwriting it. Partial failures retain raw provenance for
resumption. A single raw response is capped at 32 MiB. The implementation uses
simple Zstd Zarr-v2 AORC chunks and NetCDF4 gridMET subsets, not arbitrary Zarr.

`manifest.json` uses `GaugeBasinForcing-v1`; source byte hashes, the original
polygon and SiteSpec, exact spatial weights, interval masks and provider-day
identities accompany the export. This is a dedicated producer export, separate
from the existing NASA POWER/generic temporal workflow; its adoption is opt-in.
It is not yet connected to the QGIS plugin and is not a claim of nationwide
watershed verification.

## Consumer command

In PyTorchCPP, `Hydro/experiments/gistohq_sligo/prepare_corrected_gauge_package.py`
joins this export to an independently verified presealed native-v1 gauge
package. It preserves raw Q in m³/s, original native/quality support and retained
coarse POWER auxiliary-weather covariates; it replaces rainfall, explicitly sets
the gauge-domain area, and supplies reference ET through native-v2.

Native-v2 carries `daily_pet_utc_start_hour: 7`,
`pet_input_semantics: short_grass_reference_et`, and daily water depths in
`reference_et_mm_day`. The consumer must not treat gridMET days as UTC-midnight
days or expose an unfinished provider day. Legacy native-v1 energy-based UTC
days remain unchanged. Shared loaders make the input semantics available to
all causal forecast families; this alone does not prove a trained model or a
performance improvement.

Keep retrospective analysis inputs distinct from real-time operational
availability. AORC/gridMET are retrospective analyses, not live forecasts;
declared feature delays are experiment assumptions without archived delivery
logs. The topographic gauge basin does not prove storm-drain routing fidelity.

## Sligo development receipt

Inputs/output/scripts/raw responses are stored exclusively under HydroPINN
paper Dropbox. The 2015–2018 run uses the preserved SC15 upstream union,
16.7375 km², rather than the 29.1837 km² full domain or an old 19.0885 km² NLDI
basin. No original data/manuscript/frozen models are replaced. No access to the
separate OHQ Watershed Models folder is needed or authorized.

Sources:
- https://registry.opendata.aws/noaa-nws-aorc/ (hour-ending rain, units, source changes)
- https://www.climatologylab.org/gridmet.html (reference ET and 07UTC day)

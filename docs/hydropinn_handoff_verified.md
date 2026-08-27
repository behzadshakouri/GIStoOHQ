# Verified GIStoOHQ → HydroPINN handoff

This note records the producer-side contract that was exercised successfully against the HydroPINN consumer in `ArashMassoudieh/PyTorchCPP` using the Sligo Creek demo.

## Producer contract

HydroPINN exports are written under the selected workspace as:

```text
hydropinn/
├── manifest.json
├── variables.json
└── observations/
    ├── temporal_1.csv
    ├── temporal_2.csv
    └── temporal_3.csv
```

The current handoff uses `HydroPINNExport` schema 1.2. The manifest carries authoritative study bounds and catchment area metadata from the frozen `SiteSpec`:

```text
schema_name = HydroPINNExport
schema_version = 1.2
study_start
study_end
catchment_area_m2
catchment_area_source
```

`catchment.area_m2` and `catchment.source` therefore belong in the SiteSpec before freezing/export. The HydroPINN consumer must not infer basin area from observations or silently substitute the selected USGS gauge drainage area.

## Sligo Creek verification case

The verified demo uses:

```text
site_id: sligocreekdemo
study period: 2024-01-01T00:00:00Z through 2024-12-31T23:00:00Z
catchment area: 23,754,600 m²
selected USGS station: 01650800
```

The exported variables are:

```text
00060
PRECTOTCORR
T2M
RH2M
WS2M
ALLSKY_SFC_SW_DWN
EVPTRNS
```

The producer intentionally preserves native temporal assets and does not perform model normalization, imputation, lag construction, feature selection, or train/validation/test partitioning. Those operations remain consumer responsibilities.

## Known QC warning

The Sligo discharge record begins at 2024-01-01T01:00:00Z while the requested study start is 2024-01-01T00:00:00Z. The package therefore reports the warning:

```text
temporal.study_period_coverage
```

This warning is intentionally retained. It must not be hidden by a blanket one-hour tolerance or silent boundary imputation.

## Consumer behavior verified

The PyTorchCPP HydroPINN adapter successfully:

1. detects `HydroPINNExport` schema 1.2;
2. reads the authoritative study interval and catchment area;
3. accepts `variables.json` unit arrays;
4. reads long-form temporal CSV files using `timestamp_utc`;
5. harmonizes forcing/discharge data to hourly model rows;
6. converts observed discharge to runoff depth using the producer-supplied catchment area;
7. trains plain FFN and LSTM rainfall-runoff models.

The current field export does not contain observed storage. The consumer therefore intentionally blocks `FFN + PINN`, `LSTM + PINN`, and standalone `PINN` until a separately versioned rainfall-runoff physics profile is defined. GIStoOHQ should not fabricate storage to satisfy an older water-balance PINN contract.

## Workspace isolation

A weather/PET-only run should use a separate workspace when a truly discharge-free package is required. Reusing an append-only workspace that previously cataloged discharge can retain those earlier assets.

# Whole generated-model synthetic event ledger

Audit: `/home/arash/Dropbox/HydroPINN paper/audits/ohq_generated_event_20261119/`.
This is a development-only synthetic solver check, not research training or
forecast-skill validation. No observations were loaded. Original models,
parameters, application installation and frozen research outputs were unchanged.

## Result

All four complete historical generated formulations complete the 0.1-day event.
The equal-interval 2 mm pulse balances rainfall, external terms, boundary outflow
and the change in all non-boundary water-storage compartments:

| Formulation | Storage compartments | Rain volume (m³) | Net ledger residual (m³) |
|---|---:|---:|---:|
| legacy | 188 | 47337.600000 | -1.03e-9 |
| standard HRU | 159 | 47337.600000 | -1.81e-8 |
| mixed HRU | 246 | 47337.600000 | +7.89e-5 |
| riparian HRU | 275 | 47337.600000 | -3.48e-7 |

Acceptance tolerance is 0.01 m³ + 1e-6 of independently specified rainfall
volume (0.0573376 m³ here). The tighter event copies use
`nr_block_tolerance=1e-8`; other settings are dt=0.001 day, no timestep growth,
CN weight=0.5, nr_tolerance=1e-9. These are numerical test settings, not a change
to physical parameters. No calibration was performed.

**Variable-interval input fails independently.** Encoding exactly the same 2 mm
pulse with intervals [0,.02], [.02,.04], [.04,.1] produces 71006.4 m³: 50% too
much rainfall in every formulation. This is not an area-normalization issue.
The current `CPrecipitation::getflow(double A)` converts intervals to midpoint
rates; `Quan::CalcVal` interpolates those rates linearly. Unequal interval widths
therefore change the integrated volume. The first run's riparian ledger also
misses the strict balance tolerance (0.08467 m³) with the original default
block tolerance. Other first-run residuals: legacy -0.00556, standard +2.56e-6,
mixed +0.00291 m³. Failed cases are retained, never relabelled accepted.

The receipt's `all_cases_pass` is deliberately **false**. Next engine task is to
preserve interval totals/timing for precipitation, with a native-loader pulse
regression. Do not silently normalize rainfall or claim arbitrary forcing
intervals work A to Z. Equal-interval integrated-volume acceptance also does not
prove exact pulse timing: midpoint interpolation still smooths transitions.
No production engine change was made in this audit.

## Accounting and scope

The generated top-level area parameters sum to **23.6688 km²** (29 subbasins).
That is the sum used by these preserved historical models, not the reviewed
full-basin 29.1837 km² or the new gauge-aligned HydroPINN area. The historical
polygon and rounded model area are distinct; neither was silently replaced.

The diagnostic loads the real full composites and solver, inventories expanded
storage/inflow/link quantities and records native accepted-step values at
17-digit precision. Fixed-head Outlet's artificial 1e9 m³ storage is excluded;
its crossing link is integrated instead. All internal links cancel globally.
Declared external balance terms (including retention loss) are integrated
separately; they are not inferred from the residual. Initial stored groundwater
is included through storage change, not counted as rainfall.

Rate outputs are end-stamped: sum value*preceding-step-duration, including the
first interval even when link series have no t=0 sample. Storage requires a real
initial value. Do not apply trapezoidal integration or subtract rounded
fixed-head reservoir storage. Tests explicitly exercise first-step accounting
and a closed ledger whose independent rainfall-volume check nevertheless fails.
This is whole-domain accounting, not a separate conservation proof for every
internal link/HRU or all outflow-limiting regimes. ET sources are unassigned/zero
in these historical models. Long storms, ET forcing, real-data skill, fresh
29.2 km² project execution and HMS solver acceptance remain outside this test.

## Reproduction and saved evidence

- `scripts/ohq_generated_event_probe.cpp`: diagnostic main only; no engine patch.
  Resource path is the explicit local OpenHydroQual repository path.
- `scripts/relink_ohq_snapshot_variant.py`: builds it against hash-verified private
  snapshot objects. Use the accepted Object.cpp source-coefficient fix as the
  single object override; preserve its receipt and frozen parent archive.
- `scripts/verify_ohq_generated_event.py MODELS DIAGNOSTIC_BINARY NEW_OUTPUT`:
  creates isolated event copies and executes all eight cases sequentially.
  Expected overall exit is 1 while variable-interval cases remain broken.
- `scripts/analyze_ohq_generated_event.py CASE --area-m2 23668800 --output JSON`:
  independently checks saved native outputs, topology and rainfall volume.
- `tests/test_generated_event_ledger.py`: nine regression checks.

All eight actual runs, inventory tables, native outputs, rain files and ledgers
are saved in the audit folder. `receipt.json` binds their hashes; `provenance.json`
binds binary, template archive, build receipt and parent frozen build archive.
The new orchestration script saves the equivalent sequence; this audit's runs
were executed as two bounded batches before consolidation, not by pretending a
later-written orchestrator had already run. Original model hashes were verified.
Initial diagnostic compilation failed on a TimeSeriesSet accessor typo and was
corrected before any event run. An initial suite attempt without offscreen Qt
aborted in existing QGIS setup; the proper offscreen run passed **623 tests**, with
four existing environment/deprecation warnings. No application package changed.

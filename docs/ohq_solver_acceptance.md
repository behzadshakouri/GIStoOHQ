# OHQ solver acceptance and executable provenance

The four-formulation depth/soil-moisture rejection was isolated on19November2026
to the previously tested executable/build, not invalid generated parameters.
The existing console rejected even a minimal channel with positive depth and
an HRU with moisture strictly inside residual/saturation bounds. A clean build
from the current OpenHydroQual source accepted the identical inputs and all
four generated formulations. Deliberately negative channel length and excessive
soil moisture still return exit4. No verification bypass or parameter patch was
needed. No console/engine sources were changed; a pre-existing modified console
main was preserved and included in the source hash inventory.

The old directory's core objects were dated12November, older than the current
headers/source; build flags matched the fresh build. This establishes an
executable/build discrepancy. The precise historical source change responsible
was not bisected, so do not attribute it to one specific engine bug or claim that
moving initial-state calculation fixed it. The diagnostic checks construction,
parent binding, settings and initial-state stages; valid inputs already passed
at construction in the fresh build.

## Reproduce without changing the engine checkout

Use a NEW private build directory outside case-insensitive sync storage:

```
python3 scripts/build_ohq_initialization_probe.py OHQ_REPO NEW_BUILD QMAKE
python3 scripts/relink_ohq_snapshot_variant.py NEW_BUILD \
  OHQ_REPO/terminal/TOpenHydroQual/main.cpp NEW_CONSOLE_BUILD
python3 scripts/diagnose_ohq_initialization.py NEW_CONSOLE_BUILD/console \
  NEW_BUILD/objects/ohq_initialization_probe OHQ_REPO/resources NEW_CONTROLS \
  --expect-current
python3 scripts/verify_ohq_solver_smoke.py NEW_CONSOLE_BUILD/console \
  OHQ_REPO/resources GENERATED_MODELS NEW_SOLVER_OUTPUT
```

The diagnostic main prints criteria results before/after initialization. It does
not call Solve. The console variant uses the same frozen engine objects, with
hash checks; it compiles the supplied console main separately. Keep all source,
object, executable, resource and command hashes with the acceptance receipt.
Do not select a binary merely because its filename looks current.

The solver smoke shortens COPIES to0.001day, preserves the generated models,
enforces CPU/memory/time caps and checks finite complete output trajectories.
Exit0 alone is insufficient. The four real formulation checks produced two
output rows each, with2392/7786/8772/5002numeric columns respectively. No
observations, calibration or research fitting are involved.

## Scope and remaining limitations

The accepted binary SHA256 is
`09917edebcac96c22b397bdf46f79c13955eb9732ede7147774f9b4051ec7d93`.
The rejected older binary SHA256 was
`aa4dd3879d97b0757e2bc15039f42731487d77375264bcc7b2f8a7372504cf4f`.
Receipts, binaries and a verified case-safe frozen-build archive are retained in
HydroPINN paper Dropbox under `audits/ohq_solver_initialization_20261119/`.
No installed application was replaced, and nothing was pushed.

Each short unforced run reports `property 'area' does not exist in 'Rain'`.
The Precipitation source coefficient is an area expression evaluated in the
receiving object's context by Source::GetValue; this warning warrants separate
source-context investigation and a synthetic rainfall/mass-ledger check. It was
not suppressed or "fixed" by inventing an area on Rain. Finite startup output
does not establish correct rainfall scaling, water-budget closure, event
response or scientific skill. HEC-HMS remains structurally checked only; no HMS
solver was found on PATH. Interactive desktop and fresh DEM/full-run acceptance
remain separate.

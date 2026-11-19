# Rainfall interval-volume fix

Broader follow-up found and repaired CRLF parsing and adaptive-kernel timestep
regressions in OHQ dd730f3. See [compatibility audit](ohq_broad_compatibility.md).
The original narrow evidence below is preserved with its original scope.

OpenHydroQual commit **d7a45cb** replaces the native loader's midpoint-ramp
conversion with bounded interval-rate representation and solver-step integration.
Native precipitation is not point-resampled; ordinary time series retain their
existing interpolation. The actual code generator and runtime injection path
were updated and executed too. See OpenHydroQual tests/interval_rainfall.md.

## Independently verified

- Three small real-loader cases (unequal intervals, single bounded storm, gap),
  using0.007-day steps crossing boundaries: rainfall and storage changes match
  specified2/3m³; no rain in fully dry intervals.
- Seven malformed input cases reject at construction: negative, overlapping,
  nonfinite, trailing junk, zero-width, extra field and empty start.
- Actual generated model compiled/executed: baked and runtime-injected rainfall
  both finish at12m³ (10initial+2rain), matching interpreter; rejected injection
  leaves previous forcing intact. Final generator re-emits identical tested
  header after the last lexical parser guard.
- All eight full generated-model event cases pass (four formulations × equal/
  unequal rainfall bins), each receiving47337.6m³. Largest ledger residual
  6.915e-6m³; retained acceptance0.0573376m³. Numerical block tolerance1e-8 is
  explicit in isolated model copies, not a physical-parameter change.
- Three existing constant-rainfall/area-swap/loss ledgers pass. Existing native
  TimeSeries singleton/general-interpolation regression passes. Analytical helper
  and runtime parity test passes plain and ASan/UBSan; LeakSanitizer unsupported
  in ptrace sandbox, so leak check is not claimed.
- GIStoOHQ suite623passes,4existing environment/deprecation warnings.

Saved evidence:
`/home/arash/Dropbox/HydroPINN paper/audits/ohq_interval_rainfall_fix_20261119/`.
Earlier failed 50%-inflation receipts remain unchanged in
`audits/ohq_generated_event_20261119/`. The new `events/receipt.json` has
`all_cases_pass=true`; do not rewrite the old failure as if it never occurred.

`build_ohq_codegen_from_snapshot.py` compiles the real generator against immutable
source-pinned core objects; its receipt includes hashes and exact commands.
`verify_ohq_interval_loader.py` saves/runs the native-loader cases. The existing
full-event runner was executed against isolated copies with the tighter numerical
block tolerance added equally to both rainfall encodings. No research fitting,
observations, reserved targets or original/frozen input/output changes.

This verifies bounded synthetic interpreter/full-model rainfall accounting and
one actual generated-kernel model, not all compiled full HRUs, all default solver
tolerances, long-event/ET/limiting behavior, field skill or HMS execution. The
historical generated model area sum stays23.6688km², not29.1837km² and not the new
HydroPINN upstream-gauge normalization area. Do not silently change areas or
rescale rainfall/results. No installed application binary was overwritten.

Build notes: initial private build omitted the new untracked header and failed;
staging it allowed a fresh complete immutable build. Initial standalone generated
model compile omitted the exported runtime include path; corrected command passes.
Unrelated existing embedded-runtime makeUniform drift was preserved. The final
empty-start parser rejection uses a separate hash-bound Precipitation.cpp object;
all valid native math is identical to the eight full-event runs' snapshot.
Existing pre-modified OHQ console main remains byte-exact, unstaged. No pushes.
Research training budget remains separately pending human approval.

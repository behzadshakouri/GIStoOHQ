# Broader OHQ compatibility check — 19 November2026

The broader review found two regressions in the initial rainfall change. Both
are fixed in OpenHydroQual **3b60954** (consolidated rainfall commit):

1. A valid CRLF rainfall file in the existing plant-transpiration example was
   rejected because numeric-token validation used a spaces-only trim function.
   The parser now trims standard whitespace locally. The original file bytes,
   strict numeric consumption and negative/overlap rejection are preserved.
2. Generated rainfall integration used the adaptive BASE timestep, which can
   exceed the timestep actually taken after a clamp. It now uses `appliedDt()`.
   The base-step getter and adaptation behavior are unchanged. An independent
   closed-catchment regression reproduces0.2m³ of premature rain in the old
   kernel and passes every accepted step after the fix (max error2.63e-11m³).

## Results

| Check | Result |
|---|---|
|13existing interpreter example families|13/13complete after correction;9byte-identical before/after|
|Heat/transport analytical suite|4/4pass in both builds; all4output files byte-identical|
|Groundwater resource invariants|5/5pass in both builds, including nonzero permitted fluxes|
|Generated-model parity|6pass;2pre-existing unsupported compile cases, independently reproduced|
|Interval forcing matrix|12pass: units, calendar epoch, uniformization on/off, small/large/adaptive steps|
|Native file-format regression|10C++ cases pass, plus5valid/7invalid real-loader cases|
|GIStoOHQ regression suite|629passed,4existing warnings|

The interpreter examples cover draining tanks, parallel links, channels, fixed
heads, rule reservoirs, baseflow, snowmelt, CN catchment, HRU, urban HRU, plant
transpiration, crop uptake and bioretention. Long soil/crop/weather models were
bounded to2/7days; other selected windows were retained. Template paths/output
locations are normalized in COPIES. Observed-data/objective/parameter declarations
are removed to keep the runs forward-only with no observation loading or fitting.
No original example or forcing bytes changed. Actual nonzero ET/uptake is recorded
in5families, avoiding a vacuous dry/disabled ET check.

Nine interpreter tables remain byte-identical. Snowmelt, CN, crop and plant
examples differ with the intended rainfall timing correction. Byte differences
are not themselves proof of accuracy: interval volume/timing is checked against
independent overlap solutions, supplemented by analytical heat/resource tests.
Native solver text output is checked for finite values, unique quantities,
ordered times and complete endpoints (fixed1e-5day timestamp tolerance, not a
large tolerance proportional to the Julian epoch).

## Kernel findings — do not hide the failures

Actual generation, C++ compilation and native-vs-kernel execution pass for tank,
parallel links, rule reservoir, snowmelt, HRU and Penman plant transpiration.
Every non-rigid block is checked separately at1e-5m³+0.5%of its OWN initial/final
storage. The large artificial fixed-head boundary cannot mask small-block errors.
This extends the earlier one-small-model kernel check.

Urban and crop generated models fail to compile because intermediate link symbols
(`velocity_` and `beta_alpha_`) are missing. Rebuilt PRE-RAINFALL-CHANGE generator
produces the exact same missing-symbol sets. They are existing codegen limitations;
their native interpreter models solve. They are not counted as parity passes,
silently skipped, or labelled rainfall regressions. See preexisting_kernel_failures.json.
Fixing these unrelated generator gaps is a separate task; no global codegen/full
GUI/HMS/installed-application certification is claimed.

The first adaptive fixture did not expose the new base/applied-step bug. A stronger
forcing/end-time-clamp fixture does. Both attempts and the original snowmelt
failure remain saved, followed by the corrected results. Likewise, the initial
CRLF rejection is preserved before the successful corrected-file-format run.

The millimetre unit check also revealed the existing XString conversion table's
float32 coefficient rounding (about9.5e-8m³ on2m³). XString was NOT changed. The
independent overlap oracle explicitly accounts for that established coefficient
precision and separately records the physical-unit difference; it does not loosen
interval-integration tolerance or claim exact double-precision unit conversion.
The initial too-strict unit attempt remains saved.

## Reproduction and evidence

Audit folder:
`/home/arash/Dropbox/HydroPINN paper/audits/ohq_broad_compatibility_20261119/`.

- `examples/`: initial runs, including the CRLF regression.
- `examples_fixed/`: final13before/after cases, source hashes, strict output checks.
- `heat/`, `resource_smoke/`: isolated existing suites and analytical/invariant logs.
  The nonportable resource fixture GENERATOR was not run or its external source
  accessed: committed model/forcing fixtures only were copied.
- `kernel_parity/`: initial results, including adaptive snowmelt mismatch.
- `kernel_parity_fixed/`: final6passes/2compile failures.
- `kernel_parity_baseline/`: pre-change proof for snowmelt and the2unsupported cases.
- `applied_step_regression/`: genuine failed-before/passed-after generated binaries.
- `forcing_matrix_units_accounted/`, `native_format_checks_final/`,
  `cpp_format_regression/`: numerical/format checks.

Saved tools: verify_ohq_example_compatibility.py,
verify_ohq_kernel_compatibility.py +ohq_compatibility_parity.cpp,
verify_ohq_forcing_compatibility.py, the extended native-loader and frozen-build
scripts. All source/object/header/binary hashes and relevant build commands are
retained. Compatibility collectors now exit nonzero for failed/unsupported cases;
use their per-case receipts to distinguish pre-existing failures from regressions.
The kernel collector intentionally remains nonzero when requesting all8families.

No installed binary replacement, source-data repair/rescale, research training,
reserved observations, frozen results, manuscript edits, protected OHQ Watershed
Models access or pushes. Existing user-modified console main remains byte-exact
and uncommitted. These are bounded compatibility checks, not scientific skill
results or a guarantee for every OHQ configuration.

## Additional regression review requested by the user

Evidence: `audits/ohq_additional_compatibility_20261119/` in the same HydroPINN
Dropbox folder. No additional engine changes were needed.

- Seven chemistry/template models (CSTR, sorption and desorption) have byte-identical
  full output files before/after. Both existing analytical checkers pass on each
  build, including the deliberately invalid stoichiometric mass-balance control.
- Ten of eleven original culvert fixtures finish with byte-identical outputs,
  covering ordinary inflow/tracer time series, reverse flow, dry states and routed
  versus lumped hydraulics. T4 roadway overtopping exceeds the same30-second cap
  in BOTH builds; an earlier old-build attempt also exceeded120seconds. This case
  is unresolved, not a pass. The collector exits1 and preserves the failed cases.
  Unchanged outputs establish bounded regression compatibility, not independent
  hydraulic validation of every existing formulation.
- Eight shared-rainfall cases independently verify every accepted storage state
  for three receivers (2/1000/2300m²), reversed creation order, uniformization on/off
  and two timestep sizes. The source's coefficient is evaluated in receiver context;
  a source cannot silently reuse another receiver's area. Maximum permitted error
  is1e-7m³; all cases pass.
- Deterministic randomized oracle:1200interval fixtures and240000native/runtime
  comparisons against independent interval overlap (gaps, zero rain/area, negative
  and large calendar offsets). Optimized and ASan+UBSan runs pass at the declared
  volume tolerance1e-9+1e-7*abs(expected). Worst observed absolute error7.965e-8m³,
  relative8.646e-8. Leak detection disabled under the sandbox; not claimed tested.
- Actual CLI property verification still rejects negative area and zero roughness
  in both binaries, exit4. No permission/criteria bypass is used.
- A full FROM-SOURCE release console build at3b60954 with pinned jsoncpp submodule
  ca98c98457b1163cca1f7d8db62827c115fec6d1 succeeds. Seven chemistry outputs are
  byte-identical to the previously verified build and analytical checks pass.
  A high-precision probe linked to these freshly compiled optimized core objects
  also passes the12forcing-matrix and8shared-source cases. This is separate from
  the older frozen-object builds. Source/build commands, logs, binaries and hashes
  are preserved. Initial shadow-build attempt lacked the submodule and encountered
  existing relative-project-path assumptions; final build includes the pinned
  dependency and runs in the private source project's own directory.

Saved runners: verify_ohq_additional_compatibility.py,
verify_ohq_shared_rainfall_source.py and ohq_randomized_interval_check.cpp.
Six existing evidence-validator regressions pass. Original629-test full suite
remains the prior full-suite result; no production Python/engine code changed in
this extra review. Inputs/user edits unchanged, no installed binary replacement,
research training, reserved observations, protected-folder access or push.

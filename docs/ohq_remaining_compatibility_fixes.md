# Remaining OHQ compatibility fixes — 19 November 2026

OpenHydroQual **262a66d** fixes the failures isolated in the preceding audit.
It changes the generator, two example configurations and their documentation;
no global solver or runtime defaults are changed.

## Generator: block/link dependencies must be ordered together

Urban channel velocity and crop beta_alpha averages depend on connected link
expressions. Emitting every block before every link caused use-before-definition
errors. A global per-iteration dependency order now interleaves blocks/links
where needed, preferring ready blocks where possible. Residual locals and cached
output/transport quantities use the same order. A real cross-object cycle rejects
with an explicit diagnostic instead of producing broken code.

The minimal saved regression compiles and runs two synthetic channel cases,
with/without selected output caches. Before the fix, locals-only failed to
compile; the cached case compiled but returned stale velocities (0.0814284 for
the block versus 0.0789783 for its link). Both pass after the fix, including a
conservation check. This verifies behavior, not merely string order in a header.
Runner: OpenHydroQual/codegen/tests/test_cross_object_order.py.

All six previously supported kernel examples still pass the unchanged per-block
native/kernel gate. Urban now compiles and passes. Crop now compiles; its ORIGINAL
numerical configuration failed the strict gate only in two dry surface blocks
(native approximately -6e-5m³ versus kernel zero). No gate was relaxed. Its example
now explicitly sets nr_block_tolerance=1e-8 alongside existing nr_tolerance=1e-6.
The corrected example passes the same gate over both seven days and the complete
150-day season, all12blocks. The original failed receipt remains intact. Thus all
eight FINAL example configurations pass; do not describe the original default
crop configuration as having passed.

## Culvert: missing connection, then numerical precision

The saved T4 file had lost the Barrel→DS_Channel outlet documented by its
make_tests.py and README. Matched RELEASE controls show the original times out
(25s cap) and adding ONLY that link completes. Its physical parameters, inflow
and other settings are unchanged. This isolates topology from optimization or
global solver behavior.

The restored-only model had a5.54m³ accepted-step ledger residual on about210000m³
inflow, above the predeclared strict development gate. Setting per-block numerical
tolerance1e-8 in THIS example reduces it to-0.00037538m³, within0.218763m³, with
nonnegative storage and positive flow through both roadway and barrel. No physical
calibration or global tolerance change. Failed/default and successful/tight
receipts are both retained. The native diagnostic saves every accepted step at
17digits; link rates begin at the first step END, so that first interval is
included. Artificial fixed-head boundary storage is excluded; all other declared
external terms and the actual boundary link are accounted for.

With the corrected T4 example, all11culvert files complete and produce
byte-identical outputs in before/after interpreter binaries. This comparison
uses the SAME corrected inputs in both binaries and does not pretend that the
original missing-link input passed.

## Verification / boundaries

-8final kernel configurations pass, including the corrected full150-day crop.
-11culvert before/after comparisons complete, byte-identical.
-Actual cross-object cycle rejects with expected diagnostic.
-Locals-only and cached-output compilation/execution regression passes.
-GIStoOHQ full suite629passed,4existing warnings (fresh run this review).

Audit: `/home/arash/Dropbox/HydroPINN paper/audits/ohq_remaining_failures_20261119/`.
See final_gate_summary.json, final_provenance.json, raw failures, build receipts,
commands, generated source, binaries, strict water ledger and source snapshots.
New saved GIStoOHQ runners: verify_ohq_culvert_overtopping.py and
verify_ohq_codegen_cycle_guard.py. Original broad-review reports remain historical
records, superseded for these specific previously unresolved cases.

No research training, reserved target observations, original manuscript/frozen
outputs, protected OHQ Watershed Models folder, installed binary replacement or
push. User-modified console main remains untouched and uncommitted. No universal
OHQ/HMS/interactive-GUI certification or new predictive-skill claim is implied.

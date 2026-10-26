# Review: workflow-v3 fix commits (`26d2006..40bea9c`, `sudspy@0febe26`, `disk_to_sds@d277741`)  (2026-10-24, reviewer: Codex)

## Verdict

BLOCK.

## Findings

### F1 — blocker — PARTIALLY FIXED: the canonical wrapper is gated, but Phase 3 can still commit without a selection

- Where: `scan/phase3_driver.py:1186-1205,1224-1231`; `scan/run_production_convert.py:117-156,254-264`; legacy callers including `scan/run_phase3_pool.py:34-52`, `scan/run_recovery_register.py:108-127`, and `scan/run_production.py:87-97`
- Status: verified
- Scenario: the production wrapper now requires a selection, runs strict convert preflight, passes the selection, and refuses non-`ok` plans. Phase 3 also refuses accidental selection omission. However, `phase3_driver.py --legacy-chooser --commit` remains a direct write-capable path that deliberately converts with the defective runtime chooser and no preflight. The warning says “never production” but does not enforce it. The older launchers do not secretly add this flag; they now fail closed because none supplies a selection. Thus omission is fixed, but there is still an explicit path to committed staging output without a validated selection.
- Smallest fix or check: make `--legacy-chooser` incompatible with `--commit`, or move comparison-only legacy execution to a separate command that is restricted to an explicit test root and cannot emit a promotable run manifest. Update or retire every direct Phase-3 launcher and add a test that `--legacy-chooser --commit` is refused before importing an engine or opening an output path.

### F2 — blocker — PARTIALLY FIXED: stronger hashes are recorded, but the selection is neither plan-bound nor self-validating

- Where: `scan/coverage_planner.py:310-338,354-402`; `scan/lib/selection.py:61-170`; `scan/phase3_driver.py:1274-1303,1378-1402`
- Status: verified
- Scenario: the compiler now records a full manifest-file hash and row/day counts; Phase 3 deep-checks the manifest hash, generator commit, and selected-path membership, and the run manifest records the selection digest and three code identities. But `coverage_planner.py` still never reads the reviewed plan or an identity contract, so a selection can disagree with the reviewed channel/location/source policy. More directly, the loader treats `manifest_sha256`, `generator_sha`, `n_rows`, and `n_days` as optional. A constructed `selection-v0.3` artifact omitting all four was accepted with `deep=True`, including a selected path substituted for a different path that merely existed somewhere in the manifest. A dirty compiler also records the clean HEAD value, because `generator_sha` is only `git rev-parse HEAD`. Recording the artifact hash after conversion identifies what ran but does not establish that it was the reviewed artifact.
- Smallest fix or check: make every v0.3 provenance field mandatory; bind the selection to hashes of the reviewed plan, registry entry/identity input, and immutable manifest contents; validate each row’s station/date/source fields against the manifest, not path membership alone; and compile only after a clean/pinned preflight. Put the approved selection digest into the pre-conversion contract, then require that digest at conversion.

### F3 — blocker — PARTIALLY FIXED: channel routing is repaired, but real hour bundles and missing January tails still lose midnight data

- Where: `scan/level1.py:187-200`; `scan/lib/selection.py:190-211`; `scan/phase3_driver.py:941-960,1039-1078,1422-1434`; `scan/run_production_convert.py:145-149`; `scan/test_selection_tails.py:39-49,68-72`
- Status: verified
- Scenario: ordinary tails now carry `chans`, and the worker’s leftover bookkeeping correctly restricts non-wildcard tails; wildcard and empty-current-group cases route as separate tail-only calls without leaking a restricted channel. The bundle lookup is still wrong for the real schema, however. `level1.py` deliberately stores hourly bundles such as `23hr` with `hhmm=NULL`, while `tail_for_day()` chooses the maximum `hhmm` in each channel group. Any ordinary selected minute in that group sorts above the empty bundle value, so the selected `23hr` bundle is omitted even though it carries the midnight tail. The new test does not exercise this: it invents a “bundle” row with `hhmm='0100'` and a pathname that is not an hour-bundle filename. Separately, the production wrapper only passes a prior-year selection if it happens to exist. Without it, January 1 still returns success after dropping the opening seconds; `jan1_tail_unavailable` is not copied into the run manifest’s per-day record, so promotion cannot gate on it.
- Smallest fix or check: determine a selected row’s coverage end from its parsed bundle hour/span rather than nullable `hhmm`, and test with the actual parser output for `... 23hr ...`. Require and deep-validate the previous-year selection whenever January 1 is in scope, or make the day a non-promotable QC failure. Persist the exact tail rows and any unavailable-tail condition in the run manifest.

### F4 — blocker — PARTIALLY FIXED: probes cover more of the month, but their union is falsely assigned to every file

- Where: `scan/coverage_planner.py:141-205,208-307,340-395`
- Status: verified
- Scenario: the compiler now samples first/middle/last parts of a population-month, persists all chosen probe paths and errors, and blocks when every probe yields no channels. It then unions those probe channels and assigns that union to every unsuffixed file in the population-month. A mid-month change from Z-only to Z/N/E is therefore detected but misrepresented: early Z-only files are credited with N/E. A focused `choose_day()` counterexample made ten such disk files defeat five real N/E telemetry files, selecting no telemetry at all. The engine later finds no N/E in those disk files, recreating Class 2 loss. If one probe yields Z while the N/E-bearing probes error, the nonempty Z result also avoids the blocked state.
- Smallest fix or check: make channel evidence file- or configuration-epoch-specific. At minimum, split a population-month at observed channel-set transitions and treat conflicting/failed samples as ambiguous until every interval is evidenced. Add the brief’s DDNE/FORG before-and-after transition test and assert the chosen N/E source on both sides, not merely that the probes table contains first/middle/last paths.

### F5 — blocker — PARTIALLY FIXED: winner priority survives only inside one exact channel group

- Where: `scan/coverage_planner.py:246-303`; `scan/lib/selection.py:177-188,190-211`; `scan/phase3_driver.py:918-927,989-1029,1039-1070`; `../disk_to_sds/scripts/suds_convert.py:215-244`
- Status: verified
- Scenario: within one exact `chans` group, SQL returns winner rows before fill rows, list order reaches each engine, Python’s start-time sort is stable, and existing-first `write_sds()` preserves the first write. The worker then breaks the chain by regrouping rows by `chans` and sorting the group keys. A winner row for `NE` followed by a short-fill row for `N` executes as group `N` first and group `NE` second, so the fill’s N samples become the protected existing data and the N winner loses. This overlap of channel sets is possible because a path can win multiple orientations while another path fills only one. A path’s single `rule` is also whichever orientation assigned it first, so one row cannot express winner-for-E but fill-for-N. Boundary tails discard `rule` entirely and sort paths, losing winner priority again. The whole-minute overlay remains and the day is still written in several non-transactional calls. The sibling writer change is deterministic for identical reruns, but no disk-to-SDS regression test establishes the intended policy when a later SD-card ingest conflicts with existing samples.
- Smallest fix or check: represent priority per `(path, orientation, interval)`, then process one orientation at a time or combine all selected streams in memory and merge/write the day once. Preserve the selection’s total order across channel and format dispatch, including boundary tails. Add adversarial unequal-value tests for winner `NE` plus fill `N`, mixed formats, same-`hhmm` boundary winner/fill, and the disk-to-SDS SD-card conflict case.

### F6 — blocker — PARTIALLY FIXED: common early drift is preserved, but the one-second heuristic still changes recorded timing

- Where: `../sudspy/sudspy/utils.py:145-152,253-307`; `../sudspy/tests/test_timestamp_fidelity.py:53-95`; `scan/phase3_driver.py:225-308,311-417`
- Status: verified
- Scenario: negative accumulated drift under one second that extends the run now splits at its stated time, and the record scanner now handles per-record byte order/length, fixed-header corrections, Blockette 1001, and a QC-marked per-file fallback. The rule still trims or drops an early trace at exactly/over one second, or any early partial trace that does not extend the current run; both alter as-recorded timing without proving the trace is a duplicate. Conversely, a genuine adjacent duplicate that overlaps by 0.9 seconds and extends the run is split, leaving overlapping duplicate traces—the requested adversarial boundary is not tested and the two causes are not distinguishable by this threshold. There is also a direct fallback hole: if scan status is `split` but every segment parse fails, line 401 falls through to the plain whole-buffer read at lines 415-417, the welding path the fix says is never used.
- Smallest fix or check: do not infer duplicate-versus-clock provenance from overlap duration. Carry source-copy identity from selection and apply duplicate precedence only between known copies; within one source, preserve every stated segment boundary for either drift sign and any magnitude. Make failed split parsing enter the QC per-file path or fail, never bulk-read. Add 0.9/1.0/1.1-second duplicate and drift cases, a shortened early trace, and an all-segments-fail regression.

### F7 — blocker — PARTIALLY FIXED: output and code hashes improved, but changed source bytes can still be skipped

- Where: `scan/phase3_driver.py:657-719,928-940,1089-1106,1238-1262,1522-1569`
- Status: verified
- Scenario: all three code commits and the selection digest now enter the fingerprint, and same-size output corruption is detected by SHA-256. The selected source files themselves are still represented only by path/channel/rule. The selection binds a manifest database snapshot, not the live source bytes. If an archive file is replaced in place after scanning while the database and selection remain unchanged, the fingerprint matches and the day is skipped even though its input changed. `skipped_unchanged` is also excluded from `items_succeeded`, has no output inventory in the run manifest, and can make a fully verified rerun appear empty to the production queue. The required station-year cost measurement could not be made because SSH is blocked in this environment; hashing every output day-file on the network-backed staging root remains an unverified fast-path tradeoff.
- Smallest fix or check: bind each selected file to a live immutable identity (prefer content hash; at least validated size/mtime plus a policy for archive mutation) and verify it during the skip check. Carry verified skipped outputs into per-day inventory and the completion equation. Benchmark source/selection validation plus output hashing over a real station-year before retaining this as a speed feature.

### F8 — blocker — PARTIALLY FIXED: strict convert preflight exists, but it checks the wrong main repository on the VM and promotion remains ungated

- Where: `scan/lib/preflight.py:36-37,74-147`; `config/pinned_repo_shas.yaml:1-34`; `scan/run_production_convert.py:254-264`; `scan/run_production_promote.py:116-218`; VM layout specified in `docs/codex_dormancy_brief_2026-10-23.md`
- Status: verified
- Scenario: nested untracked code is now enumerated, code roots reduce irrelevant scratch failures, strict mode rejects null pins, and the convert wrapper invokes it. On the VM, however, the executing checkout is named `eqserver_2_seiscomp_v3`, while the configured repository is `eqserver_2_seiscomp`. `check_repo()` recognizes the current repository only when those basenames are equal; otherwise it checks the sibling old checkout. Consequently the preflight either blocks against the wrong commit or, if that checkout is placed at the pin, leaves the executing v3 tree unchecked. The promote wrapper still never calls `run_preflight('promote', strict=True)`, and the convert preflight record is printed but never embedded in the run manifest. All configured pins remain null, so strict conversion currently fails safe rather than being production-ready.
- Smallest fix or check: map the main-repository config entry explicitly to `REPO_ROOT`, independent of checkout directory name, and add a test where the checkout has the VM’s `_v3` suffix. Run strict preflight inside both canonical convert and promote commands, require full exact pins, and carry the receipt into the run/promotion provenance.

### F9 — blocker — NOT FIXED: chooser, converter, and auditor still share the critical reader and naming logic

- Where: `scan/coverage_planner.py:177-196`; `scan/qa_verify.py:224-305`; `../disk_to_sds/scripts/suds_convert.py:27-51,93-144`
- Status: verified
- Scenario: no independent witness was added. Channel probing still imports `qa_verify.read_source_file`; the auditor and converter still share `sudspy.read_suds_stream`; and auditor/engine still use the same SEED-passthrough rule. The running study therefore cannot detect a common parser, payload-length, channel-label, or accepted-code mistake at the decisions that determine channel presence.
- Smallest fix or check: add the independent low-level SUDS block/channel/sample-count witness already accepted in the prior review, with no imports from sudspy, qa_verify, or suds_convert, and compare its identities to reviewed StationXML/plan inputs.

### F10 — blocker — PARTIALLY FIXED: audit errors fail closed, but resume and scoring still mix or overstate evidence

- Where: `scan/triage_study.py:168-223,250-307`; `scan/qa_verify.py:560-675`
- Status: verified
- Scenario: comparator/no-database outcomes are now ERROR, empty channel evidence cannot PASS, unknown findings fail closed, ERROR is retried, and each new line names three repository HEADs. Resume nevertheless marks every earlier PASS/FAIL terminal before computing or comparing those identities, so restarting after a code change still mixes versions. Results do not record manifest or selection digests, and dirty code is indistinguishable from its HEAD. Cached selections are checked only for generator HEAD; a changed manifest with the same code causes repeated conversion errors rather than recompilation. The acceptance bound is still aggregate `missing + selection_loss <= 2 seconds × channel count`, so one channel can lose nearly the entire multi-channel allowance. Rare strata are still drawn only from the first 400 shuffled station-days.
- Smallest fix or check: define a complete run identity (clean code SHAs, sample digest, manifest digest, selection digest, scoring version), resume only matching terminal lines, and rebuild any selection whose full validation fails. Apply the loss bound independently per channel. Build rare strata from exhaustive indexed predicates or explicitly label the study as a bounded random sample.

### F11 — blocker — NOT FIXED: the gate evidence is still running and could not be independently inspected

- Where: `STATUS.md` “SAVE-POINT 2026-10-23”; `docs/qa/triage_evidence_map.md`; VM paths in `docs/codex_dormancy_brief_2026-10-23.md`
- Status: verified
- Scenario: the full 656-day rerun and the last proving legs were still in flight at handoff; the independent witness and Path-A dress rehearsal remain open. This review environment rejects SSH to the VM with `Operation not permitted`, so the raw study/proving outputs could not be checked. Even a clean study result would not close the code defects above, especially the month-union counterexample and study-resume identity bug.
- Smallest fix or check: after the code findings are resolved, run the named proving cases and full study from a clean pinned identity, retain raw results and digests in durable evidence, complete the independent witness and BEST/WLSH/CLIF dress rehearsal, and submit the interpretation for another gate review.

No nit-only findings.

## Checked and found OK

- The new lightweight suites all passed locally: conversion gates, selection-tail loader refusals, and preflight side-load gates. Python syntax compilation passed for the six changed Python targets. ObsPy-dependent tests could not run locally because no available interpreter has ObsPy.
- The canonical production conversion wrapper performs strict preflight before creating queue/output directories, requires one named selection per unit, passes it to Phase 3, and filters station plans to reviewed `status: ok`; F1 and F8 cover the remaining escape and preflight identity defects.
- Phase 3 deep-validates a normal compiler-produced selection before dispatch and never falls back to `query_cross_source_day_files()` from selection mode. Repository-wide search found no wrapper that secretly supplies `--legacy-chooser`.
- Ordinary non-wildcard boundary rows retain their channel restrictions. The leftover routing handles a Z-only tail beside an NE current group without feeding Z into NE; an empty current day can still recover a selected prior-day sliver; a wildcard tail is isolated in an unrestricted tail-only call. F3 covers the bundle and January-1 failures.
- Probe paths, errors, and basis are persisted, `--no-probe` selections are refused, probe-empty populations block conversion, and pre-v0.3 artifacts are refused. F4 covers the still-invalid population-month union.
- Within one exact channel group and one format, winner rows remain ahead of fills through reader order and stable merge order. Existing-first merge-on-write makes identical reruns deterministic. F5 covers priority loss between groups/formats/tails and the untested SD-card conflict policy.
- The miniSEED record scanner now reads byte order and record length per record, applies unapplied fixed-header corrections and Blockette-1001 microseconds, and sends an unparseable scan to a QC-marked per-file read. Positive drift and the common extending negative-drift case have explicit tests; F6 covers the threshold and failed-split holes.
- Output integrity is now content-hashed, so a same-size modified SDS file cannot satisfy `_outputs_intact()`. Pre-fix ledger entries without hashes are refused. F7 covers live source identity, manifest inventory, and unmeasured cost.
- The code reviewed here does not write the origin archive or long-term SDS directly, and Phase 3 remains dry-run by default. No production action was performed during this review.

## Response

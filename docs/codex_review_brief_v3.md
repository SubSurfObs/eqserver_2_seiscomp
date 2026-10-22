# Comprehensive v3 review brief (the hard gate before DU)

**Operator order (Dan, 2026-10-21): the ENTIRE v3 code set gets a full
independent review before DU converts in anger. This document is the scope
for that review. It does not replace `docs/review_brief.md` — read that
first; it sets your rules, priorities and output format. This file only says
WHAT to review and WHICH questions matter for this specific gate.**

Write the output to `reviews/<date>_v3_full_gate.md`. Verdict semantics:
PROCEED means DU conversion may start (subject to Dan's own sign-offs);
anything else blocks DU.

## What v3 is, in one paragraph

v3 (branch `workflow-v3`, VM clone `~/projects/SubSurfObs/eqserver_2_seiscomp_v3`)
replaces the v1 defect "the converter does not obey the reviewed plan" with a
fail-closed chain: a chooser (`scan/coverage_planner.py`) reads the Level-1
station DB and writes a per-station-year **selection SQLite** — the exact
files to convert per day, per channel; the converter (`scan/phase3_driver.py
--selection`) refuses to run without a valid selection and converts ONLY what
it lists; the auditor (`scan/qa_verify.py`) independently re-reads the EqServer
sources and measures what the output is missing. Timestamps are converted
exactly as recorded on disc — never corrected (operator ruling 2026-10-21).

## Review targets (all on `workflow-v3` unless stated)

| # | Target | What changed / what it does |
|---|---|---|
| 1 | `scan/coverage_planner.py` | The chooser. Populations (source_type × grammar), per-file channel knowledge (suffix parse + content probes, `N_PROBES=4` union), per-orientation winner by minute coverage, no-alternation gap fill, sub-minute short-file fill (commit `5313ae2`), probes + manifest_identity tables. |
| 2 | `scan/lib/selection.py` | Loader + fail-closed validation (schema_version, complete marker, net/sta/year, manifest_identity binding). |
| 3 | `scan/phase3_driver.py` | `--selection` mode; `_apply_orient_filter`; per-format engine dispatch for mixed days (`2e760cc`); reader-level timestamp drift-split (`de8f94f`); in-memory corrupt-file fallback; skip-unchanged fingerprints + `.converted_days.jsonl` ledger; `--run-manifest`. |
| 4 | `sudspy` commit `8d7b5db` (sibling repo) | `fast_merge_split` contiguity now judged against the emitted grid (`run_start + n·delta`), drift > 0.5 sample starts a new segment. Tests: `tests/test_timestamp_fidelity.py`. |
| 5 | `disk_to_sds` commit `dba4f10` (sibling repo) | SEED-code passthrough (`_SEED_SEISMO_RE`) in `suds_convert.py` — colleague-authored, Claude-reviewed, committed from a side-load. |
| 6 | `scan/qa_verify.py` | The auditor. Exhaustive mode via `QA_PROBE_EVERY=1 QA_EXTRA_CAP=…`; SEED passthrough regex must match the engine's. |
| 7 | `scan/triage_study.py` | The stratified convert+audit study driver (656 days); synth plans for DU/VX. |
| 8 | `scan/lib/preflight.py` + `config/pinned_repo_shas.yaml` | Per-stage SHA pinning; untracked `.py`/`.sh` under scan paths aborts (side-load defence). |

Evidence to judge against (not explanations): the triage study results at
`~/v3_test/triage_study/results.jsonl` on the VM, the one-day audit harness
`~/v3_test/fix_one_day.sh`, and the proven cases recorded in
`docs/qa/triage_evidence_map.md` and `STATUS.md`.

## Gate questions (answer each explicitly, verified vs suspected)

1. **Fail-closed, really?** Find any path where phase3 converts a day the
   selection does not admit, or falls back to its own file choice when the
   selection is missing, stale, partial, or built from a different station
   DB than the one being converted. The v1 defect must be structurally
   impossible, not just untriggered.
2. **Chooser correctness.** Can the per-orientation winner ever pick a copy
   whose channel set was mis-probed (probe union misses a channel present
   only in unprobed files)? Can short-file fill select a file whose minutes
   overlay rather than fill (re-introducing the alternation-sliver loss)?
   Is the 0.88×median short-file threshold justified or magic?
3. **Timestamp fidelity.** Both weld layers (sudspy merge + phase3 reader
   drift-split): any remaining path where obspy joins records across a
   drifted boundary? Does the 0.5-sample threshold interact badly with
   rates ≥ 250 Hz? Confirm NO code path "corrects" a timestamp (ruling:
   as-timestamped, always).
4. **Auditor independence.** Does qa_verify share a reader, a regex, or a
   selection assumption with the converter such that a shared bug passes
   both? Exhaustive mode: does it truly read every source file's payload?
5. **Mixed-format dispatch.** A day with both SUDS and miniSEED: does every
   population reach its correct engine, and is the combined output trimmed,
   merged and written once (no engine overwriting the other's day file)?
6. **Skip-unchanged.** Can a changed input (new file in the day dir, edited
   selection, different engine SHA) ever hit a stale fingerprint and skip?
7. **Preflight.** Can production run with uncommitted/unpinned code in any
   of the four repos? Is the untracked-file check scoped so a side-load is
   caught but a scratch dir does not cause false aborts forever?
8. **Study validity.** Do the strata in triage_study actually cover the
   loss modes in `docs/qa/triage_evidence_map.md`? Is PASS defined tightly
   enough (bad samples = 0, missing+selection-loss < 6 s)?

## Known open items (do not report as discoveries; check they are tracked)

- Fix 5 not built at brief-writing time: identity-driven channel naming
  override (plan codes must override `_enforce_band_instrument`'s
  band-by-rate for DU loc-60 `EH*` stations). If built by review time,
  it is IN scope as target 3/5.
- Closure equation + inventory promotion: designed, not built (post-break).
- Stray-day rule (bogus 1900/1970/rollover dates): specced, not built.
- nconnect mount not in fstab (reverts on VM reboot).

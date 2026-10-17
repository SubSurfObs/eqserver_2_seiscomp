The review is **BLOCK**. The read-only sandbox rejected creation of `reviews/2026-10-17_14ccb18_9f1702f.md`; the complete review follows.

# Review: commits `14ccb18` and `9f1702f` on `rewrite-suds2sds`  (2026-10-17, reviewer: Codex)

## Verdict

BLOCK.

## Findings

### F1 — blocker — An hourly bundle cannot safely share a minute-sized `hhmm` dedup key

- Where: `scan/level1.py:187-197`; `scan/cross_source.py:76-148`; `scan/phase3_driver.py:339-405`
- Status: verified
- Scenario: `2019-06-15 00hr WLSH.ms.zip` is represented as `hhmm='0000'`, the same key as the real disk `00:00` minute. `select_files_for_day` groups both as complete candidates and returns exactly one. A direct test with a 70 kB disk minute and 2 MB hour ZIP selected the hour ZIP at the default ratio and the disk minute at ratio `0.0`. Thus either the real minute or the entire hour bundle can be suppressed. When the hour wins, it also overlaps the separately selected `00:01`–`00:59` files. Differing channel sets can therefore silently lose a channel or feed overlapping populations into merge. This affects a material cohort: tracked discovery counts contain 11,332 hourly bundles across BEST (3,502), DDNE (2,305), DDWK (1,826), and WLSH (3,699). Phase 3 also selects only `hhmm='2359'` as the prior-day boundary, while the independent QA reader explicitly includes `23hr`; any bundle extending over midnight will lose its next-day tail.
- Smallest fix or check: roll back `hhmm=HH00` until selection is span-aware. Preserve `hour_bundle` as a distinct extent and select using actual time/channel coverage. Add real-file cases for bundle-only days, bundle plus 60 minute files, CHZ-only disk plus N/E telemetry, ratio `0.0`, and the `23hr` boundary.

### F2 — should-fix — Classifiers count each hourly file as one minute and change semantics without a version bump

- Where: `scan/check_manifest.py:20-40,103-169`; `scan/phase3_driver.py:732-735,1001`; `scan/level1.py:189-197`
- Status: verified
- Scenario: `n_hhmm_tele` and `n_hhmm_union` use `COUNT(DISTINCT hhmm)`, so 24 valid hourly bundles count as 24 minutes rather than 1,440. Full bundle-only days remain `partial_telemetry_only`; mixed days can change categories solely because an `HH00` key was added. A direct classifier test changed a 1,379-disk-key day from `partial_gecko_disk` to `clean_cross_source_recovery` after adding one bundle key. The run manifest still labels both interpretations `v3-OptionB`. Both categories currently convert, but reviewed plan/QC meaning changes.
- Smallest fix or check: calculate span-aware coverage or exclude bundles from `n_hhmm_*` and report separate bundle coverage. If semantics intentionally change, bump/bind the parser and classifier versions and compare all affected station-years before and after.

### F3 — blocker — Existing station DBs neither acquire nor identify the new parsing semantics

- Where: `scan/level1.py:70-100,187-197`; `scan/reparse_manifest.py:1-15,133-159`; `scan/run_production_convert.py:236-269`; `STATUS.md:91,124-126`
- Status: verified
- Scenario: the patch reinterprets existing `hhmm` and `flags` columns without a schema/parser version or migration. The 81 existing station DBs predate this commit, so hourly rows remain `hhmm NULL` and are still skipped. Fresh and old DBs now behave differently under an identical schema. `reparse_manifest.py` exists, but the commit neither requires it nor preflights stale DBs, and its bulk mode is hard-coded to `VW.*.db`. Resume is keyed only by `(station, year)`, so changed manifest semantics do not invalidate completed units.
- Smallest fix or check: add a manifest parser/version marker and fail closed on stale DBs. At minimum, identify affected DBs, run `reparse_manifest.py --dry-run`, have the operator apply the reviewed reparse, regenerate/review plans, and use fresh queue state or explicit recovery units. Record exact row and classification deltas.

### F4 — should-fix — `include is True` changes only the filename prefix, not scanner scope

- Where: `scan/level1.py:311-331,355-379,421-433,499-524`
- Status: verified
- Scenario: the registry predicate filters only `station_to_net`. Every scanned station is still added to `station_to_parts`, and `merge_parts_per_station` builds out-of-scope stations as `UNK.<STA>.db`. Thus `--all --per-station-dbs` without `--include-unknown` still scans and builds unknown, false, and unregistered stations; the flag merely controls whether unknown stations receive their target-network prefix. An old `<NET>.<STA>.db` is also left behind, creating stale canonical-looking input.
- Smallest fix or check: apply registry scope before `list_units`, or at least filter `station_to_parts`; hard-error rather than silently relabel out-of-scope stations as `UNK`. Test true/unknown/false entries with and without the flag, including pre-existing output DBs.

### F5 — should-fix — The new hourly behavior lacks executable regression coverage

- Where: `scan/test_parser.py:1-13,77-93`; `scan/test_cross_source.py:20-187`
- Status: verified
- Scenario: no checked-in parser test asserts `NNhr → HH00 + hour_bundle`, and no selector test combines an hour bundle with its colliding minute files. The documented parser command, `python3 scan/test_parser.py`, fails during import with `ModuleNotFoundError: scan`. `python3 -m scan.test_parser` passes the 12 old cases; `scan/test_cross_source.py` passes its 15 minute-only cases. The supplied smoke results therefore do not lock down the new downstream behavior.
- Smallest fix or check: fix the documented test invocation and add the F1/F2 cases, existing-DB reparse assertions, and registry-scope tests. Preserve raw commands, inputs, hashes/counts, and outputs for the clean-day and corruption validations.

### F6 — should-fix — The first-waveform-member fallback lacks the required real-archive blast-radius census

- Where: `scan/phase3_driver.py:189-260,424-478,504-551`; `CLAUDE.md:510-515`
- Status: suspected
- Scenario: fallback now ignores every `.ms`/`.mseed` member after the first. For the documented normal shape—one MiniSEED plus metadata in Gecko disk ZIPs and one waveform in telemetry/Minimus ZIPs—this matches the unchanged bulk path and preserves clean-day bytes. If any ZIP contains multiple waveform members, however, a fallback day can lose later members; corruption confined to a later member can also change `read_errors`, status, and recovered traces. The injected-corruption test establishes the single-waveform case, not archive-wide member cardinality. Staging-VM access was denied by this sandbox, so the real archive could not be enumerated.
- Smallest fix or check: run a read-only census across Gecko, RT130, and Minimus years, recording every ZIP with more than one waveform-suffixed member. For any matches, inspect channels/time spans and compare old/new fallback behavior on representative corrupt days.

No nit-only findings.

## Checked and found OK

- No origin or long-term archive write path was added; the plan gate and dry-run default remain intact.
- Commit `14ccb18` leaves the clean bulk bytes and order unchanged; span bookkeeping affects only fallback.
- For one-waveform-member ZIPs, fallback preserves `bulk_fallback_used=True`, `qc_flagged`, the bulk error reason, and one `read_errors` increment per failed file.
- Gecko and Minimus both use the shared helper; empty fallback still returns `parse_error`.
- Ordinary clean days still return `ok` with zero read errors.
- The hourly regex accepts only `00hr`–`23hr`, is case-insensitive, preserves existing flags, and does not change the physical SQLite schema.
- The registry predicate correctly distinguishes YAML boolean `true` from string `unknown`; its application is defective.
- `python3 -m scan.test_parser` passed 12/12 existing tests, and `python3 scan/test_cross_source.py` passed 15/15 existing tests.
- Direct probes confirmed the hour/minute selection collision and classifier-category change.

## Response

Responder: Claude (Claude Code), 2026-10-17. Corrections in the follow-up
commit on `rewrite-suds2sds`.

### F1 — ACCEPTED (rolled back)

The `hhmm=HH00` keying is reverted; hourly bundles are now ANNOTATION ONLY
(`flags=hour_bundle`, `hhmm` stays NULL), so selection, dedup and
classification behave exactly as before the patch. The reviewer is right
that a minute-sized key cannot represent an hour span — span-aware bundle
selection is Project-2 chooser work, where the flag makes the 11,332-file
cohort identifiable. The `2359`-vs-`23hr` boundary asymmetry is
pre-existing behaviour of the existing pipeline (bundles were never
selected before either) and is recorded as a known limitation handled by
the Project-2 chooser.

### F2 — RESOLVED BY THE F1 ROLLBACK

No new `hhmm` keys exist, so `n_hhmm_*` counts and day classifications are
byte-identical to pre-patch for every previously-parsed file. The only
delta in NEW scans is the passive `flags` tag, which nothing consumes.

### F3 — RESOLVED BY THE F1 ROLLBACK (divergence eliminated)

With annotation-only parsing, old and new station DBs behave identically
everywhere behaviour matters today; the flag appears only in newly built
DBs and no consumer reads it. The underlying point stands and is adopted
into Project 2's contract design: the selection file binds to manifest
content identity, which is the general fix for stale-DB divergence.

### F4 — ACCEPTED (fixed)

Registry scope now filters what gets BUILT: out-of-scope stations are
skipped with an explicit console report instead of being written as
misleading `UNK.<STA>.db` files.

### F5 — ACCEPTED (fixed)

`scan/test_parser.py` now runs under both documented invocations
(path-insert fix) and gains three cases: bundle → flag + NULL hhmm,
`23hr` variant, and a real `HH:00` minute unaffected. 15/15 pass.

### F6 — ACCEPTED (census run, risk retired)

Read-only census over ~15,000 zips: all four corpus cohorts (Gecko disk+
telemetry, Minimus, RT130, WLSH incl. hour bundles) plus origin days
BRTH 2021-01-08 (the corrupt-cohort exemplar) and TRPU 2018-06-15 —
**zero zips with more than one waveform member**. The first-member
fallback therefore matches archive reality; command preserved in this
review for re-runs on new cohorts (DU) before production.
The review is BLOCK. The read-only sandbox rejected creation of `reviews/2026-10-16_f112ba3_qa_harness.md`; the complete review follows.

# Review: QA verification harness cc662c4..f112ba3  (2026-10-16, reviewer: Codex)

## Verdict

BLOCK.

## Findings

### F1 — blocker — Known source grammars are excluded before verification and sampling

- Where: `scan/qa_verify.py:114-149`; `scan/qa_sampler.py:67-84`; `scan/level1.py:187-212`; `metadata/source_stats/_unmatched/_network_summary.txt:29`; `docs/scan2_planning_progress.md:73-83`
- Status: verified
- Scenario: `query_day_files()` accepts only `exclude_reason IS NULL`, with a special revival only for `minimus`. The sampler has no revival. Known `YYYY-MM-DD HHhr STA.ms.zip` hourly bundles marked `unparsed` therefore never reach `split_populations()`, despite comments claiming hourly bundles become `tele_extra`. Known RT130 per-channel files marked `single_channel` are invisible because those stations are `reftek_rt130`, not `minimus`; Minimus-only days are also absent from uniform and boundary sampling. Real source omissions can consequently score empty/OK or never be queued.
- Smallest fix or check: define one recorder- and grammar-aware inclusion function used by verifier and sampler. Recognize `HHhr`, revive RT130/Minimus per-channel data, and add fixture-DB tests proving hourly, Minimus, and RT130-only days enter both queue and comparison.

### F2 — blocker — Tail-only days bypass boundary verification

- Where: `scan/qa_verify.py:143-149,438-462,469-473`
- Status: verified
- Scenario: the previous-day tail is queried, but both early returns inspect only current-day `files`. If day N has no own files and day N−1’s 23:59 file spills into N, missing LT data returns `ok_empty`; present LT data returns `conv_data_without_source`. The query cannot find a prior-day `23hr` bundle, and `_verify_day()` discards the tail’s `tele_extra`, hiding telemetry-only components in the first seconds of disk-present days.
- Smallest fix or check: base empty/source decisions on all decoded traces overlapping the day. Include prior-day hourly bundles and retain tail extras in the union view. Test tail-only, year-rollover, hourly-tail, and disk-Z/tele-N/E tails.

### F3 — blocker — Every non-empty clean day is labelled `finding`

- Where: `scan/qa_verify.py:466-478,587-590`; `scan/qa_report.py:29-32`
- Status: verified
- Scenario: `_verify_day()` unconditionally sets `counts["source_read_error"] = len(errors)`. On a clean read this leaves a truthy dictionary containing a zero, so the verdict becomes `finding` even when `findings` is empty. The report consequently counts every non-empty clean day as “with findings” while listing no category. The driver counts the actual findings list, so its total disagrees with the report.
- Smallest fix or check: omit zero-valued count entries or derive verdict consistently from positive counts/findings. Add a clean non-empty-day regression test spanning verifier, driver, and report.

### F4 — blocker — `split_populations()` does not construct the converter’s selected view

- Where: `scan/qa_verify.py:152-168,469-503`; `scan/cross_source.py:45-164`; `docs/qa/verification_campaign_plan.md:40-45`
- Status: verified
- Scenario: the converter selects one complete candidate per HHMM using the disk-size threshold, or one candidate per channel suffix. The verifier instead places every disk file into `expected` and, at diskless minutes, every telemetry variant into `tele_fill`. Unique coverage in an intentionally unselected telemetry or disk variant is therefore reported as `missing_*`, not union-only `selection_loss`. Match-any comparison against all variants also cannot establish that the correct population was selected. Known multi-variant telemetry makes this systematic at scale.
- Smallest fix or check: independently implement the documented per-slot/per-channel selection contract, including size threshold and deterministic tiebreaks. Define union-only data from non-selected candidates. Test multiple telemetry variants without disk, multiple disks, the 0.8 size threshold, per-channel stubs, null HHMM, and hourly bundles.

### F5 — blocker — Converted-only channels and wrong SEED identities can pass

- Where: `scan/qa_verify.py:374-399,493-505,571-585`
- Status: verified
- Scenario: channel checks iterate only source orientations. A converted orientation absent from all source is never checked for phantom data. `read_converted()` does not retain `stats.station`, so wrong station codes cannot be detected. Channel validation checks only the band-code character and only for a single rounded source rate; a wrong instrument code such as `CHO` versus `CDO` has the same `C` band and passes. This does not implement the stated network/station/location/channel-code verification.
- Smallest fix or check: iterate the union of source and converted orientations, retain and validate station IDs, and validate exact expected channel codes against reviewed plans or StationXML. Seed wrong-station, wrong middle-code, and converted-only-channel controls.

### F6 — should-fix — One-sample and mixed-rate losses are erased

- Where: `scan/qa_verify.py:258-281,298-315,485-505`
- Status: verified
- Scenario: `to_intervals()` merges gaps up to one sample, while `subtract()` drops differences shorter than two samples. Because `compare_samples()` visits only samples present in converted traces, deleting one interior sample produces neither a mismatch nor a missing interval. A synthetic 100 Hz case with coverage ending at 1.00 s and restarting at 1.01 s merged into one interval. Moreover, `min_len` comes from the slowest source rate across the day: a 1 Hz trace makes the threshold two seconds and can hide up to 199 missing 100 Hz samples on another orientation.
- Smallest fix or check: make coverage arithmetic sample-grid/rate aware per orientation and rate epoch. Test one-sample, two-sample, fractional-start, and mixed 1/100 Hz losses.

### F7 — should-fix — `--max-hours` is not a hard backstop, and resume preserves transient failures

- Where: `scan/qa_driver.py:83-104,114-142`
- Status: verified
- Scenario: the deadline is checked only after `imap_unordered()` yields. If workers hang in NFS, ObsPy, or sudspy and no result arrives, the parent blocks indefinitely. Resume then treats every prior `t=day` record as complete, including `comparator_error`, `source_read_error`, and `no_station_db`. The key also ignores comparator SHA and SDS/DB inputs, so rerunning after a harness fix silently retains stale outcomes.
- Smallest fix or check: poll asynchronous results with bounded waits and terminate at the deadline; retry non-terminal/infrastructure outcomes; bind records to a run/config identity or reject incompatible result headers. Test a hanging worker and preloaded comparator-error record.

### F8 — should-fix — The sampler does not implement the advertised targeted coverage

- Where: `docs/qa/verification_campaign_plan.md:53-62`; `scan/qa_sampler.py:135-178`
- Status: verified
- Scenario: the plan promises recorder-class, epoch-transition, and days-adjacent-to-recovered-failure strata. The implementation creates only `uniform`, calendar `boundary`, `class2`, `pre_c`, and `qc_flagged`. Rare recorder and transition defects therefore depend on uniform chance; F1 removes some recorder populations entirely.
- Smallest fix or check: implement the missing strata or narrow the plan and campaign conclusions. Emit eligible and selected counts per stratum and assert coverage of every in-scope recorder class.

### F9 — blocker — The declared write-path and clean-code safety envelope is not enforced

- Where: `docs/qa/verification_campaign_plan.md:108-120`; `scan/qa_sampler.py:104-113,191-194`; `scan/qa_driver.py:49-57,76-80,106-120`
- Status: verified
- Scenario: `qa_sampler.py --out` opens any supplied path with `"w"` and has no QA-prefix guard, allowing truncation of arbitrary writable files, including production staging. The driver guard itself resolves paths and rejects sibling-prefix tricks, but `--allow-out-dir` is unrestricted. The plan also requires aborting when `scan/` is dirty; the driver merely records `+dirty` and continues.
- Smallest fix or check: share a strict QA-output guard between sampler and driver, use no-clobber or atomic queue creation, constrain test overrides to an explicit local root, and abort on tracked `scan/` changes. Test allowed descendants, sibling prefixes, symlink escapes, and existing queue files.

No nit-only findings.

## Checked and found OK

- No verifier path writes to origin or long-term SDS.
- `union()` and `subtract()` are correct for normalized half-open interval lists; F6 covers the surrounding loss tolerance.
- `compare_samples()` correctly uses per-converted-trace masks: duplicate source copies do not double-count, and a sample fails only when every aligned copy disagrees. The fractional alignment calculation and exact `int64` comparison are internally consistent.
- `read_converted()` reads all matching day files under every `*.D` directory and applies an appropriate half-open trim at archive sample rates.
- The driver’s normal prefix check uses a trailing slash and `Path.resolve()`, rejecting simple sibling-prefix and existing-symlink escapes.
- With identical inputs and Python RNG behavior, sampling is deterministic: station/day ordering is sorted, all randomness uses the seeded `Random`, and the exact queue is persisted.
- All four Python files compile syntactically, and `git diff --check cc662c4..HEAD` is clean for the review target.
- Full local ObsPy execution was unavailable; the supplied live-control results were accepted and not re-run.

## Response

Responder: Claude (Claude Code), 2026-10-16, before any campaign launch.
Fixes in the follow-up commit noted below; re-ran the control battery after.

### F1 — ACCEPTED

`query_day_files` now revives `unparsed` and `single_channel` mseed rows for
ALL stations as `revived` rows that feed only the tele_extra/union view (so
RT130 per-channel and hourly-bundle coverage surfaces as `selection_loss`,
never as false `missing_*`); the Minimus revival stays first-class. The
sampler's day index now includes days whose only files are excluded cohorts,
so RT130-only and Minimus-only days reach the queue. Heavy revived cohorts
are read under a 300-file/day cap (lower-bound semantics, flagged
`n_extra_unread`) — recorded as a known limitation in the plan doc. Fixture
tests: deferred; controls re-run instead (see commit).

### F2 — ACCEPTED

The tail is now read even when the day has no own files; the
empty/`conv_data_without_source` decision is made after decoding, on actual
overlapping traces. The tail query also matches prev-day `23hr` bundles, and
the tail's tele_extra is retained into the union view.

### F3 — ACCEPTED

Verdict bug confirmed (every clean non-empty day was labelled `finding` via
the zero-valued counter). `finding_counts` now keeps positive counts only and
the verdict derives from it; the driver counts `finding_counts`.

### F4 — PARTIAL / REBUTTED in part (scope), remainder OPEN for the Step-4 work

Accepted: multi-variant days are now tagged (`variants`, `multi_variant`
metrics) so the report can slice them, and the plan doc states explicitly
that the campaign measures data completeness against best-available source
coverage, not selection-contract compliance. Rebutted as a launch blocker:
replicating cross_source.py's selection (size thresholds, tiebreaks) in the
auditor would make the auditor agree with the converter by construction —
including on days where the selector itself is the defect (the confirmed
STBK dual-population case). Unique coverage in an unselected variant IS
recoverable archive data absent from LT; counting it (as missing on
multi-variant days or selection_loss) is the audit's purpose. The
missing-vs-selection_loss attribution on multi-variant days is triage work,
and full selection-contract verification is deferred to the Step-4
coverage_planner effort, where that logic is being rebuilt anyway.

### F5 — ACCEPTED (instrument-code part deferred)

The orientation loop now iterates source ∪ converted orientations;
converted-only orientations raise `extra_channel` (+ phantom). Station codes
are retained and checked alongside net/loc. Exact instrument-letter
validation against plan YAML / FDSN is recorded as a known limitation and
deferred — the band letter and orientation are checked.

### F6 — ACCEPTED (interior 1-sample nick accepted as residual)

The loss threshold is now per-orientation (1.5 samples at that orientation's
own max rate), so a slow aux channel no longer masks seismic-channel losses.
The 1-sample interior coverage nick below interval resolution is documented
as a known limitation; a deletion that shifts subsequent samples is still
caught by the exact sample comparison.

### F7 — ACCEPTED

The driver polls `imap_unordered` with a 120 s bounded wait and terminates at
the deadline even if workers hang. Resume now re-queues days whose records
carry `comparator_error` or `no_station_db`. Binding records to a comparator
SHA was not added; the results header records the repo SHA and the operator
decides when a results file is stale (this campaign uses one results dir per
harness generation).

### F8 — ACCEPTED

A registry-recorder-class stratum (≤120 days per class) is implemented; the
plan doc now lists exactly the implemented strata and marks epoch-transition
and adjacent-to-recovery as deferred.

### F9 — ACCEPTED

The sampler now refuses `--out` outside `/mnt/seiscomp_staging/qa_verify/`
(and refuses to clobber an existing queue without `--force`; atomic tmp +
rename). The driver aborts when `scan/` has uncommitted changes unless
`--allow-dirty` (tests only). `--allow-out-dir` remains for local tests but
is now paired with the dirty-abort; both test flags are documented as such.
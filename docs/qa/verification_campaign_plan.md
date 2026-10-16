# QA verification campaign — randomized source-vs-converted testing

**Status: DRAFT — awaiting Dan's approval of the safety envelope and scope.**
Written 2026-10-16. Intended to run over the weekend of 2026-10-17/20 with
findings ready for Dan's attention Monday 2026-10-20.

## Goal

Before converting the bulk of DU, verify the work already done (v1 VW/VX
promotions) and the current engine, at the **sample level**: for randomly and
strategically sampled (station, year, day) units, open the original archive
files (PC-SUDS via sudspy, miniSEED via ObsPy), open the converted SDS
day-files, trim to the common window, and compare — identical integers,
missing/extra data intervals, timing, channel/network/location codes. Surface
problems, then diagnose them into categories mapped onto the 12 defect
classes in `docs/v1_lt_defects_and_v2_resweep_scope.md`.

This is also the empirical half of the assumptions inventory: every
comparator check encodes an assumption the conversion relies on.

## Shape: deterministic harness does the mass work, agents do triage

A code harness (not LLM agents) performs the per-day comparisons — it's an
I/O-bound mechanical job and needs to run tens of thousands of times. Agent
effort goes where judgment is needed: triaging new findings into categories,
investigating one exemplar per category, re-weighting the sampler toward hot
spots, and writing the findings register. Claude checks in on a loop every
~1–2 h while the sweep runs.

## Components (all new, under `scan/`)

1. **`scan/qa_verify.py` — the comparator.** Input one (net, sta, year, day).
   - Locates source files via the Level-1 station DB (`~/station_dbs/`),
     never by filesystem walk.
   - Reads originals: `.dmx[.gz]` via sudspy `read_suds_stream`; `.ms.zip` /
     `.mseed.zip` via ObsPy with read-whole-file-into-RAM zip handling.
     Deliberately does NOT import `phase3_driver` / `suds_convert` — the
     comparator must be an independent code path. (Residual risk: sudspy
     itself is shared with the engine, so a sudspy sample-decode bug is
     invisible to this test. Noted, accepted for now.)
   - Builds two source views per channel: (a) the **selected** view applying
     the documented disk-preferred dedup, (b) the **union** view across all
     sources. (b)−(converted) catches selection losses (Class 2 signature).
   - Reads the converted day-file (LT `/mnt/seiscomp_archive`, ro, in Mode A;
     sandbox SDS in Mode B), trims both to `[day, day+1)`.
   - Compares: exact int32 equality on overlapping samples; interval diff
     (missing-in-converted, phantom-in-converted, with boundary vs mid-day
     position); per-channel presence; SEED id fields; duration ratio.
   - Emits one jsonl record per (day, channel): `ok` or findings with
     category + evidence. **Flushes incrementally** per item
     ([[feedback-scans-write-at-intervals]]).

2. **`scan/qa_sampler.py` — stratified random sampler.** Universe = the 357
   promoted v1 units × their days-with-data (from station DBs, including
   days whose only files are excluded cohorts such as RT130 per-channel or
   hourly bundles). Seeded RNG, queue written to the results dir for
   reproducibility. Implemented strata: uniform, year-boundary days
   (Jan 1 / Dec 31), Class-2 candidate stations (WLSH, WDSD, BRTH, DDSW,
   DDNE, DDWK, FORG, BEST), pre-Option-C units (convert ts before
   2026-06-02), `qc_flagged` days from run manifests, and
   registry-recorder-class coverage (≤120 days per class). *Deferred to a
   later queue build:* epoch-transition days and days adjacent to
   recovered failures.

3. **`scan/qa_driver.py` — pool driver.** `multiprocessing` workers
   (start ~8, cap ~16 — shared NFS), resume-by-default (skips days already
   in results jsonl), heartbeat log, launched with the canonical setsid
   detach recipe so it survives SSH disconnect.

4. **`scan/qa_report.py` — rollup.** Counts by category × stratum ×
   recorder, exemplar paths, coverage achieved. Feeds the triage loop and
   the Monday report.

## Problem taxonomy (initial; triage may extend)

`missing_day` · `missing_channel` · `missing_interval_boundary` (midnight /
year-boundary) · `missing_interval_midday` · `sample_mismatch` ·
`timing_shift` · `duplicated_data` (duration ratio ≫1) ·
`selection_loss` (union view has data converted lacks — Class 2) ·
`seed_id_mismatch` (net/sta/loc/chan) · `phantom_data` ·
`source_read_error` · `comparator_error` (our bug, not a data finding).
Each finding carries enough evidence (paths, UTC ranges, sample offsets) to
re-derive it.

## Controls before mass launch (falsifiability, S0-style)

The comparator is not trusted until it:
- **flags known defects**: ≥2 days from the Class-3 pre-C cohort must show
  `missing_interval_boundary` ≈ SS seconds; a WLSH/WDSD Class-2-era day
  must show `selection_loss` if the telemetry N/E really exists there;
- **clears known-good days**: ≥5 spot-verified clean days (e.g. the v2
  BEST/WLSH rechecked days) must come back `ok`;
- passes a seeded-corruption test: copy one LT day-file to scratch, perturb
  samples/truncate, point the comparator at the scratch copy, confirm
  detection of each category.

## Modes

- **Mode A (first, default): audit existing LT.** Source vs promoted LT
  bytes. Zero writes except results/logs. This directly answers "is the
  existing work right?"
- **Mode B (optional, after Mode A is healthy): engine re-run.** Re-convert
  sampled days with the pinned engine into a sandbox SDS and 3-way compare
  (source / sandbox / LT). Tests the *current* engine incl. fast_merge_split
  trim semantics. Requires Dan's exact engine-pin decision (Codex F3:
  `20a483c` checkout vs pin `ec159ea`).

## Safety envelope (the thing Dan approves)

- Hosts: staging VM only. **dev1 untouched.**
- Reads: origin NFS (ro), LT (ro on this host by mount), station DBs.
- Writes: ONLY under (a) results dir
  `/mnt/seiscomp_staging/qa_verify/<YYYY-MM-DD>/` (queue, results jsonl,
  logs), (b) Mode B sandbox
  `/mnt/seiscomp_staging/qa_verify/<YYYY-MM-DD>/staging_sds/`. All write
  paths guarded by an assert-prefix check; no rmtree anywhere
  ([[feedback-no-default-wipe-in-loops]]).
- No ledger touches, no queue-jsonl touches, no production staging tree
  writes.
- Provenance: results header records SHAs of eqserver_2_seiscomp, sudspy,
  disk_to_sds (Mode B) + dirty/clean state; abort if eqserver repo dirty in
  a way that touches `scan/`.
- Known VM dirt (the 2026-06-24 diagnose outputs) gets pulled/committed
  BEFORE launch so the provenance line is clean.

## Review loop (per AGENTS.md checkpoints)

- Codex reviews the comparator + sampler code **before mass launch**
  (checkpoint 2: verification code).
- Codex reviews the Monday findings interpretation (checkpoint 3).

## Timeline

| When | What |
|---|---|
| Thu eve (on approval) | Build comparator/sampler/driver; commit VM diagnose outputs; local tests |
| Fri | Controls on the VM; Codex review; fix; launch Mode A detached |
| Fri–Sun | Sweep runs; Claude triage loop every ~1–2 h: categorize new findings, investigate exemplars, re-weight sampler if a category gets hot; interim notes to `docs/qa/` |
| Sun | If Mode A healthy and pin decided: Mode B on a subset |
| Mon | `docs/qa/2026-10-20_findings.md`: counts by category, exemplars, mapping to defect classes 1–12, recommended fixes; STATUS.md updated |

## Throughput / coverage expectation

Dominant cost is reading originals (an EchoPro day ≈ 1,440 gz files over
NFS; Minimus ≈ 4,320). At ~1–2 min/station-day × 12 workers, expect roughly
5–15 k station-day comparisons across the weekend — a few percent uniform
coverage of the ~110 k converted station-days, plus full coverage of the
targeted strata. The sampler design means the campaign is resumable and
extendable: more coverage is just more weekend.

## Known limitations (agreed residual risk, Codex review 2026-10-16)

- **Selection-contract compliance is not verified.** The comparator audits
  *data completeness against best-available source coverage* (expected =
  disk + telemetry gap-fill; everything else = selection_loss). It does
  not replicate the converter's per-slot candidate selection
  (cross_source.py size thresholds / tiebreaks), so it cannot distinguish
  "engine lost data" from "selector picked the other variant" — that split
  is triage's job, aided by the per-day `multi_variant` metric. Full
  selection-contract verification belongs with the Step-4
  coverage_planner work.
- **Single-sample interior dropouts are below interval resolution**
  (coverage merges at ~1 sample, loss threshold 1.5 samples at the
  orientation's own rate). A deleted sample inside a converted trace that
  shifts later samples WILL show as sample_mismatch; a 1-sample coverage
  nick will not.
- **Instrument-code letter is not validated** (band letter is; orientation
  is). CHO-vs-CDO-class naming questions need the plan YAML / FDSN
  integration — deferred.
- **Revived excluded cohorts (RT130 per-channel, unparsed grammars) are
  read with a 300-file/day cap**, so their selection_loss seconds are a
  lower bound on heavy days (flagged via `n_extra_unread`).
- sudspy is shared with the conversion engine, so a sudspy sample-decode
  bug is invisible to this audit.

## Out of scope

- DU conversion itself (blocked on the 7 owed decisions).
- Fixing anything found — findings are triaged and registered, fixes are
  separate, operator-sequenced work.
- PPSD/data-quality QC (post-conversion science QC is a different pass).

## Decisions needed from Dan to launch

1. Approve the safety envelope above (one-time; everything after runs in
   background).
2. Mode A only, or Mode A then Mode B? (Mode B also needs the engine-pin
   choice from Codex F3.)
3. Results dir on the staging share as proposed, or VM local disk?

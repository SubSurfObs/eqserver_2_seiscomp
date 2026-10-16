# v1 LT defects and v2 resweep scope

Distilled from `docs/scan1_recovery_register.md`, the convert_failed/held
queue, and the discoveries during v2 build-out (2026-06-17 → 2026-06-24).
Used to scope the v2 resweep: only resweep stations whose v1 LT exhibits
defects v2 can actually fix.

For each class:
- **Sig**: how to spot it in LT
- **Known**: concrete affected stations/years
- **Criterion**: how to enumerate more
- **v2 fix?**: yes / partial / no (and why)
- **Priority**: HIGH (must resweep) / MED / LOW / SKIP (resweep won't help)

The catalogue updates as new defect classes surface. Add new classes here
when found, do not edit existing entries except to mark status.

---

## CLASS 1 — Side-load engine fragmentation (the big one)

- **Sig.** A single LT day-file has many ObsPy traces (50–450+) with
  ~1.0–1.6 s gaps between them, instead of 1 contiguous trace per real
  outage period. Most acutely visible on Gecko-recorder station-years
  converted in the 2026-06-01 → 2026-06-12 window.
- **Known.** STBK 2022-10-23 (450 traces); BEST 2019-10-07 (44 traces);
  all Gecko/Minimus station-years converted in the side-load window.
- **Criterion.** Walk `promote_done.jsonl`, for each (sta, year) check
  `run_manifest.json` started_at within 2026-06-01 → 2026-06-12, AND
  station recorder ∈ {gecko, minimus}. Confirmed-affected: **56**.
  Candidate (registry empty recorder type): **+264**. Operator floor
  from 2026-06-18 night: "~100 station-years is the realistic floor."
- **v2 fix?** **YES**. The committed engine merges per-minute files into
  1 trace before write, and `fast_merge_split` reliably handles
  pathological days that previously timed out. Verified end-to-end on
  v3 test catalogue + BEST today.
- **Priority.** **HIGH**. This is the canonical reason to do a v2 resweep.
  Also serves as an opportunity to retire the engine-provenance incident.

## CLASS 2 — Disk-misconfig: 1-channel disk + 3-channel telemetry

- **Sig.** LT has **CHZ-only** for a station-year while v2 staging shows
  CHE+CHN+CHZ. Or LT CHZ count >> CHN/CHE counts within a year.
- **Known.** From diagnose `_epochs` data:
  WLSH (~5 yrs 2018-2023), WDSD (~4.5 yrs 2019-2023), BRTH (~5 yrs
  2018-2023), DDSW (~2 yrs 2017-2019), DDNE (~3 yrs 2017-2020),
  DDWK (~2 yrs 2017-2019), FORG (~1 yr 2021), BEST (1 yr 2019-2020,
  briefly).
- **Criterion.** For each station, scan `_epochs/<STA>.json`'s
  `by_source_kind`: any disk_* epoch with `channels` strictly smaller
  than overlapping tele_* epoch. Or query LT counts directly: CHN ≠
  CHE ≠ CHZ within a year.
- **v2 fix?** **DEPENDS on planner**. The engine and plan-generator now
  have channel-epoch awareness. WLSH run in flight today will confirm
  whether v2 actually picks up the telemetry CHN/CHE that disk lacks.
  **If WLSH confirms fix, priority HIGH.** If not, the fix is still
  in development.
- **Priority.** **HIGH (pending WLSH confirmation)**.

## CLASS 3 — Midnight-boundary loss (pre-C unit-years)

- **Sig.** Every day-file in LT starts at `T00:00:NN.000` (with NN
  = the recorder's SS offset) instead of `T00:00:00.000`. ~12 s/day/
  channel typical.
- **Known.** 32 unit-years that ran before the C trim landed
  2026-06-02 10:20 AEST — full list in register Issue 1. Includes
  HOLS 2022/2023, all BEST years 2017-2025, all BRIG years, most BRTH.
- **Criterion.** `run_manifest.json` `started_at < 2026-06-02T00:20Z`.
  Or read first trace start-time per day-file; flag if not `T00:00:00`.
- **v2 fix?** **YES in engine**. But the BEST spot check (720 files
  today) showed v2 sample counts EQUAL v1 LT sample counts — so either
  v1 BEST didn't actually lose 12 s, or v2 isn't catching the boundary
  either. **Need to verify on a known-affected unit (BRIG 2023) before
  scaling.**
- **Priority.** **MED**. Mechanical low-data-volume class but real
  ~3.65 hr/station-year if v2 actually adds it back.

## CLASS 4 — Test-run-clip (HOLS-shaped silent partial-year)

- **Sig.** LT has only 1–7 days of a year that should have hundreds.
  `run_manifest.json` `argv.start_date`/`end_date` < full year.
- **Known.** HOLS 2022 (7 days), HOLS 2023 (7 days). Both recovered
  2026-06-18 via re-conversion (Issue 49 → DONE).
- **Criterion.** `run_manifest.json` `argv.start_date != "YYYY-01-01"`
  AND no `partial_completion_reason`.
- **v2 fix?** **YES + orchestrator guard now in place** (Issue 50
  landed). Future runs refuse to mark partial-year complete without
  explicit reason.
- **Priority.** **LOW**. Already recovered for known cases; v2 resweep
  not needed for this class.

## CLASS 5 — Silent zero-byte clusters

- **Sig.** LT has near-complete year except a contiguous cluster of
  days where every channel is missing. `run_manifest.json`
  `per_date_status` shows those days as `status: ok` but
  `bytes_written: 0`.
- **Known.** KRAN 2012 (65 days, scattered); DDSW 2017 (16 days from
  2017-10-04); DDWK 2017 (same 16 days as DDSW — regional/upstream
  event); LOYU 2016 (1 day).
- **Criterion.** Walk run_manifests, count days with `status: ok` AND
  `bytes_written: 0`. Cluster threshold ≥ 5 consecutive.
- **v2 fix?** **DEPENDS — needs source-level investigation.** Could be
  classifier-vs-engine disagreement (level1 said clean, engine found
  nothing usable). Or genuine source pathology not captured at level1.
- **Priority.** **MED** for DDSW/DDWK 2017 (the regional cluster is
  interesting). LOW for KRAN/LOYU (small/known scope).

## CLASS 6 — Timeout/parse_error/error (sparse 1–3 day blips)

- **Sig.** Specific days missing from LT, with corresponding
  `convert_failed.jsonl` entry or `per_date_status` non-OK.
- **Known.** BRTH 2020/2021/2024; CLIF 2018; DDSW 2019/2021; DDWK
  2021; FORG 2021; HOGN 2019; LOYU 2017; LRSE 2021. Plus
  DDNE/DDSW/LOYU/LRNW/LRWS `rc=-9` history.
- **Criterion.** `convert_failed.jsonl` entries with no resolved event.
- **v2 fix?** **YES**. Current engine pin includes the STEIM2 INT32
  fallback (no more glitch-sample failures) and `fast_merge_split` is
  deterministic on all 224 catalogue days with 0 timeouts.
- **Priority.** **MED**. Many small wins; mostly station-years already
  recovered via per-day retries (Issue 40).

## CLASS 7 — CLIF 2018-01-16 STEIM2 spike

- **Sig.** CLIF 2018 missing exactly one day (2018-01-16) per channel.
- **Known.** CLIF 2018-01-16.
- **Criterion.** Specific.
- **v2 fix?** **YES**. INT32 fallback (`disk_to_sds@88323ec`) handles
  glitch samples that exceed STEIM2 30-bit dynamic range.
- **Priority.** **HIGH if rerunning CLIF anyway**, but trivial scope.

## CLASS 8 — Bogus pre-2012 year-leak

- **Sig.** LT has files under `<YEAR>/VW/...` where `YEAR < 2012`.
- **Known.** Status NEEDS ASSESSMENT — never audited whether anything
  landed.
- **Criterion.** `find /mnt/seiscomp_archive/{1900..2011}/VW`.
- **v2 fix?** **YES in engine** (`_filter_bogus_year_traces` at
  `eqserver_2_seiscomp@22443b7`). But the fix is preventive; existing
  stray bytes are an LT cleanup task, not a resweep target.
- **Priority.** **LOW** (LT cleanup, not v2 work).

## CLASS 9 — HDDL pre-2018 + MRDN post-2018-04-27 mis-labels

- **Sig.** Specific files in wrong-station directories.
- **Known.** HDDL pre-2018 (3 files, 2012-01-04); MRDN
  2018-11-20 + 2019-01-11 (6 files).
- **Criterion.** Specific.
- **v2 fix?** **NO**. Not a conversion bug — upstream EqServer ingest
  labelling. Pure LT cleanup. Delegated 2026-06-11 to
  `seiscomp_server_uom`.
- **Priority.** **SKIP** for v2 resweep.

## CLASS 10 — Gecko/Minimus mid-day sample-rate change

- **Sig.** Convert errored with "differing sampling rates" message;
  day missing from LT.
- **Known.** DDBE 2019-12-16 (1 day).
- **Criterion.** Engine error pattern in v1 logs. Pre-DU Level 2
  header scan is the systematic way to surface more.
- **v2 fix?** **NO YET**. Engine fix is queued in disk_to_sds but
  not landed. A v2 rerun would hit the same error on the same day.
- **Priority.** **SKIP** until engine fix lands.

## CLASS 11 — SOMU dual-sensor Trillium data drop

- **Sig.** SOMU 2019 has 61 day-dirs where source has only c04-c06
  files (Trillium velocity) but LT is empty for those days because
  the converter drops c04-c06 as "aux".
- **Known.** SOMU 2019 (61 days, the entire affected scope per
  Issue 8 audit).
- **Criterion.** Specific to SOMU; not present at other VW stations.
- **v2 fix?** **NO**. Requires registry `secondary_sensor`
  annotation + engine support for second SDS location code. Park
  until generalisable.
- **Priority.** **SKIP** until annotation + engine support lands.

## CLASS 12 — KRAN 2012 c04-c06 accelerometer-only

- **Sig.** Looks like missing days; actually correctly dropped.
- **Known.** KRAN 2012 (65 days).
- **Criterion.** Specific.
- **v2 fix?** Not a bug. The 65 days really have only
  accelerometer; no velocity data exists.
- **Priority.** **SKIP** — nothing to fix.

## CLASS A (source-level, NOT a v1 defect)

For completeness: the intrinsic Gecko-firmware-era gaps
(DDNE/BRTH 2017-2019) are real source loss. ~5 gaps/minute × 1440
× ~7200 gaps/day/channel. LT faithfully reflects source. No
re-conversion brings them back. Operator decision 2026-06-18: **do
not act on Class A.**

---

## What to do with this catalogue

**Strong candidates for v2 resweep** (where v2 produces measurably
different LT):

| Class | Stations | Approx station-years |
|---|---|---|
| 1 Side-load fragmentation | 56 confirmed Gecko/Minimus | 56 |
| 1 Side-load candidate | 264 registry-empty | up to 264 |
| 2 Disk-misconfig | WLSH WDSD BRTH DDSW DDNE DDWK FORG | ~20 |
| 3 Midnight-boundary | 32 specific unit-years | 32 |
| 5 Silent zero-byte (DDSW/DDWK 2017) | 2 station-years | 2 |
| 6 Sparse failures | scattered | ~10 |
| 7 CLIF 2018-01-16 | 1 day | (trivial) |

**Overlap matters** — many stations appear in multiple classes (e.g.
BRTH is in classes 1, 2, 3, 6). De-duplicated, the union is probably
**~30–80 station-years of meaningful v2 resweep value**, not 41 × 12.

**Stations where v2 adds nothing (BEST today proved this)**:
- v1 LT already byte-faithful in sample counts
- Trim-vs-average byte differences don't promote (apply.py is sample-
  count-based)
- Resweeping these is wasted CPU

**Detection plan** for the v2 resweep scoping:
1. Per-station LT-defect scoring: walk run_manifests + per-station LT
   directory, flag each (sta, year) against the criteria above.
2. Score-based ranking: stations with highest defect counts → resweep
   first.
3. Confirm with one validation per class (e.g. WLSH for class 2,
   BRIG 2023 for class 3, a Gecko station-year in side-load window
   for class 1).
4. Cohort-roll-out by validated class.

# Scan 1 (VW network) — Recovery Register

**Purpose.** Single living document tracking every issue surfaced during the
2026-06-01-onwards production sweep of the VW network that will require
recovery work after the main sweep completes. Each scan of a different
network gets its own register (next: `scan2_DU_recovery_register.md`).

**How to use this document.**

- Append a new `## Issue N` section every time a new bug or data-loss
  cause is identified mid-sweep. Don't edit existing issues retroactively
  unless their status or affected-scope changes.
- Each issue follows the same fixed shape — keep it. Future scans copy
  the schema so cross-scan comparisons (Issue 1 here ↔ Issue 1 next
  scan) are mechanical.
- The affected-scope criterion should be **derivable**: a query over
  `run_manifests/`, `convert_done.jsonl`, `convert_failed.jsonl`, or the
  Level-1 manifest. Hard-coded enumerations are snapshots that drift —
  always include the criterion alongside the snapshot.
- Status transitions: PENDING → IN PROGRESS → DONE. Don't delete done
  issues; future scans benefit from seeing how prior recoveries went.

**Cross-references.**

- `CLAUDE.md` "Sweep recovery registers" — index of all per-scan files.
- `sweep_status.py` — live operational view; this register is the
  durable narrative.
- `convert_failed.jsonl`, `run_manifests/*.json` — raw event sources.

---

## Issue 1 — Midnight-boundary data loss in pre-C units

- **Detected:** 2026-06-02 (operator identified that EqServer minute-files
  straddle midnight via the SS-offset; verified by inspecting BRIG 2023
  staging output where every CHZ day-file started at exactly
  `00:00:12.000000Z`).
- **Root cause:** `write_sds` wrote each day's per-channel SDS file from
  scratch via overwrite. The 2359 file of day N-1 carries the first SS
  seconds of day N; day-job N-1 wrote that sliver into day-N's SDS file
  correctly, but day-job N then OVERWROTE the same file from scratch
  without the sliver, losing SS seconds at the start of every day.
- **Fix landed:**
  - **C** (eqserver-side trim): `eqserver_2_seiscomp@2724c15`, 2026-06-02
    09:58:23 +1000 AEST. Day-job N pulls day-1's 2359 file in as a
    boundary tail and trims the merged stream to `[day, day+1)` so each
    SDS file is written by exactly one job.
  - **B** (disk_to_sds-side read-merge-write): `disk_to_sds@00b6835`
    (pulled in via `disk_to_sds@2ee96f3` on VM at 2026-06-02 ~10:44 AEST).
    `write_sds` now reads existing day-file records and merges before
    atomic write-back. Captures both midnight boundaries by construction.
- **Affected scope.** Criterion: any unit whose phase3 subprocess
  started BEFORE 2026-06-01T23:58:23Z (the C commit + VM-pull moment).
  Identifiable via the run-manifest filename's `YYYYmmddTHHMMSSZ` field.

  Snapshot at 2026-06-02 (32 unit-years; sorted by phase3 start time):

  | # | Unit | phase3 start (UTC) |
  |---|---|---|
  | 1 | HOLS 2023 | 20260531T125240Z |
  | 2 | HOLS 2022 | 20260531T131432Z |
  | 3 | BEST 2025 | 20260531T132306Z |
  | 4 | BEST 2024 | 20260601T043643Z |
  | 5 | BEST 2023 | 20260601T050048Z |
  | 6 | BEST 2022 | 20260601T054337Z |
  | 7 | BEST 2021 | 20260601T064946Z |
  | 8 | BEST 2020 | 20260601T072338Z |
  | 9 | BEST 2018 | 20260601T082526Z |
  | 10 | BEST 2017 | 20260601T092755Z |
  | 11 | BRIG 2024 | 20260601T093502Z |
  | 12 | BRIG 2023 | 20260601T100510Z |
  | 13 | BRIG 2022 | 20260601T114928Z |
  | 14 | BRIG 2021 | 20260601T125559Z |
  | 15 | BRIG 2020 | 20260601T140316Z |
  | 16 | BRIG 2019 | 20260601T145327Z |
  | 17 | BRIG 2018 | 20260601T154730Z |
  | 18 | BRIG 2017 | 20260601T171423Z |
  | 19 | BRTH 2024 | 20260601T172322Z |
  | 20 | BRTH 2023 | 20260601T183616Z |
  | 21 | BRTH 2022 | 20260601T195115Z |
  | 22 | BRTH 2021 | 20260601T204030Z |
  | 23 | BRTH 2020 | 20260601T212122Z |
  | 24 | BRTH 2019 | 20260601T221658Z |
  | 25 | BRTH 2018 | 20260601T230947Z |
  | 26 | BRTH 2017 | 20260601T234630Z |
  | 27 | BRTH 2014 | 20260601T234631Z |
  | 28 | BRTH 2015 | 20260601T234631Z |
  | 29 | BRTH 2016 | 20260601T234631Z |
  | 30 | BRTH 2012 | 20260601T234632Z |
  | 31 | BRTH 2013 | 20260601T234632Z |
  | 32 | CLIF 2024 | 20260601T234633Z |

  First post-C unit (no boundary loss; do NOT recover): CLIF 2023
  (20260602T002019Z, 2026-06-02 10:20 AEST). All subsequent units have
  C in effect; from CLIF 2023's successor onward, B is also in effect.

  Magnitude per unit: depends on the SS pattern across the year. Typical
  EchoPro/Gecko with median SS ≈ 12 loses ~12 s/day/channel ≈ 3.65
  hr/station-year across 3 channels. BRIG 2023 verified at exactly 12 s
  every CHZ day-file; BRTH 2019 verified at 14-22 s (mixed-SS year).
- **Recovery strategy.** Option A — post-sweep boundary-stitch pass:
  1. For each affected (station, year) above, re-run phase3 with C
     active against the original EqServer NFS source. The boundary-tail
     query + trim will pull the day-1 2359 file in and capture the
     missing first SS seconds. Output overwrites staging with corrected
     day-files.
  2. Run `apply.py --mode decide` against the corrected staging. With
     B active, `write_sds` will read the existing LT file, merge the
     boundary samples in, atomic-write back. The override gate
     (`held.jsonl`) handles any cases where the staging vs LT diff is
     larger than expected.
  3. Cleanup staging after promote.

  EqServer NFS source is read-only and byte-immutable, so every boundary
  sample is still recoverable.
- **Status:** PENDING — queued for post-sweep.
- **Notes.** Eqserver-side fix verified end-to-end on the VM at
  2026-06-02 via CLIF 2023's in-flight staging output (every CHZ day-file
  past day 052 starts at exactly `T00:00:00.000000Z`); CLIF 2024 staging
  comparison shows the pre-fix `T00:00:NN` leakage on the same station.
  See `CLAUDE.md` "Midnight-boundary data loss" and
  `handoffs/disk_to_sds/2026-06-02_midnight-boundary/`.

---

## Issue 2 — BRTH gecko parse_error days (single-record corruption)

- **Detected:** 2026-06-01, during BRTH multi-year processing. ~13 days
  across BRTH 2020-2024 failed phase3 with `status=parse_error` in the
  run-manifest's `per_date_status`.
- **Root cause:** ObsPy `read()` of the bulk-concatenated mseed buffer
  crashes on any single corrupted Steim record. One bad record per day
  took down the whole day's read.
- **Fix landed:** `eqserver_2_seiscomp@a6894fb` — per-file fallback in
  `convert_gecko_day` and `convert_minimus_day`. When the bulk read
  raises, the day falls back to per-file reads; only the corrupt
  individual file(s) are dropped and logged in `read_errors`. Validated
  in production on BRTH 2019-04-11 (recovered 5,395 traces from a day
  that previously failed).
- **Affected scope.** Criterion: any run_manifest with a
  `per_date_status` entry where `status == "parse_error"`. Identifiable via:
  ```
  ls /mnt/seiscomp_staging/eqserver_sweep/run_manifests/*.json |
    xargs -I{} jq -r '.eqserver.per_date_status[] |
        select(.status=="parse_error") |
        "\(input_filename) \(.date)"' {}
  ```
  Known so far: ~13 BRTH days across 2020-2024 (per sweep_status output).
  More may surface as later units finish; query at recovery time, not
  now, for the authoritative list.
- **Recovery strategy.** Day-level retry pass:
  1. Enumerate all parse_error days via the jq query above.
  2. Group by (station, year). For each group, re-run phase3 with
     `--start-date`/`--end-date` clipped to those days (or pass
     `--dates-file`).
  3. The per-file fallback path will now succeed.
  4. Apply.py promotes the new staging output; the override gate will
     handle the original parse_error days (which had zero LT bytes
     written) as straightforward writes.
- **Status:** PENDING — queued for post-sweep.
- **Notes.** This is independent of Issue 1: parse_error days had
  ZERO bytes written to LT in the first pass (phase3 returned
  parse_error before write_sds ran), so the recovery is a simple
  write, not an override.

---

## Issue 3 — BEST 2019 NULL hhmm cross_source crash

- **Detected:** 2026-06-02 mid-sweep. BEST 2019 phase3 exited rc=1 with
  `TypeError: '<' not supported between instances of 'str' and 'NoneType'`
  in `scan/cross_source.py:87`.
- **Root cause:** Level-1 manifest had row(s) with NULL `hhmm`; the
  cross-source dedup sort comparison crashed when an hhmm value was None.
- **Fix landed:** `eqserver_2_seiscomp@9a6abe4` — skip NULL-hhmm rows at
  cross_source input.
- **Affected scope.** Criterion: `convert_failed.jsonl` entries that
  predate `9a6abe4`. Known: BEST 2019 (exactly one unit).
- **Recovery strategy.** Unit-level retry:
  1. (DONE) VM `disk_to_sds` reconciled to `2ee96f3` (2026-06-02).
  2. (PENDING) `engine_git` source-dict schema bump in `apply.py` (per
     [[project-du-sweep-preconditions]] memory). Until this lands, the
     events.jsonl line for BEST 2019's retry will not have the engine
     SHA stamped — acceptable for one unit but should land before the
     bulk DU sweep.
  3. Re-run phase3 + apply.py for BEST 2019 from scratch. Since BEST
     2019 had zero LT bytes written, no override-gate concern.
- **Status:** PENDING — VM reconcile done; awaiting engine_git schema
  + retry execution.
- **Notes.** Per `sweep_status.py`, the retry CLI is auto-generated when
  failed_keys is non-empty.

---

## Issue 3b — DDNE 2017 phase3 deadlock (single bad day stalled the pool)

- **Detected:** 2026-06-04 ~14:00 AEST. DDNE 2017 had been listed
  "in flight" for ~22 hours with no log activity. Diagnosis: phase3
  emitted 364 of 365 day-lines, missing day `2017-10-07`. All 6 phase3
  processes alive but in `futex_wait_queue_me`/`pipe_read` with 0%
  momentary CPU — classic multiprocessing.Pool deadlock with one worker
  hung mid-day-job and the parent waiting forever.
- **Root cause:** unknown specifically. Day `2017-10-07` on a Minimus
  per-channel-per-minute station triggered some pathological state
  inside an ObsPy operation (likely `Stream.merge()` given the DDBE
  precedent — see Issue 5 below). The worker neither errored, nor
  returned a result, nor crashed; it just sat sleeping. Not in D-state
  (no NFS hang), so it's a Python-level deadlock, not an I/O block.
- **Fix landed:** none yet. Mitigation: operator-initiated SIGKILL of
  the phase3 process group 2026-06-04. convert.py recorded `FAIL DDNE
  2017 rc=-9` and advanced through DDNE 2016-2012 (all empty
  pre-deployment years) to DDSW 2024 within seconds.
- **Affected scope.** Criterion: convert log entries with `rc=-9`.
  Known: DDNE 2017 (one unit). The specific day **2017-10-07** is the
  poison input that needs isolated investigation in the retry pass.
- **Recovery strategy.** Unit-level retry, similar to BEST 2019:
  1. Retry phase3 for DDNE 2017 from scratch with the post-fix engine.
     If the bug is in `Stream.merge()` for some pathological trace
     pattern, this will hang again on 2017-10-07.
  2. If the retry also hangs, isolate that single day: run phase3 with
     `--dates-file` listing only `2017-10-07` to reproduce. Then
     either (a) skip that day and convert the other 364, or (b) add
     a per-day timeout watchdog to the worker so a single stuck day
     doesn't block the pool.
- **Status:** PENDING — queued for post-sweep retry pass.
- **Notes.** Suggests we should add a per-day-job timeout to the
  multiprocessing pool so a future hang fails the day cleanly instead
  of stalling the whole unit indefinitely. Filing as a follow-up
  against `scan/phase3_driver.py:_worker_convert_day`. The DDBE
  2019-12-16 issue (Issue 5) is a related but different failure mode
  — that one raised an exception visibly; this one hung silently.

---

## Issue 4 — Bogus pre-2012 trace year-leak

- **Detected:** 2026-05-31, during the first sweep attempt. BEST 2024
  phase3 wrote traces with starttime year 1989/1970/1999 into
  `/staging/1989/...` etc., despite the unit's path-year being 2024.
- **Root cause:** Some EqServer-era recorder files have valid path-dates
  but contain SUDS traces whose internal starttime fell back to a
  pre-GPS-fix default (often 1989, 1970, 1999). `write_sds` filed each
  trace by its `stats.starttime.year`, so these landed in wrong-year
  subtrees.
- **Fix landed:** `eqserver_2_seiscomp@22443b7` —
  `_filter_bogus_year_traces()` drops traces whose starttime year is
  below `DEFAULT_MIN_DATA_YEAR=2012` before reaching write_sds. Counts
  are surfaced as `bogus_year_traces_dropped` in run-manifest
  per_date_status.
- **Affected scope.** Criterion: any phase3 run that predated `22443b7`
  AND had `bogus_year_traces_dropped > 0` in its per_date_status (if
  the field was even being captured then) OR — more reliably — any
  pre-2012 year-dir under `/mnt/seiscomp_staging/seiscomp_archive/`
  or `/mnt/seiscomp_archive/` that contains VW files.

  Check via:
  ```
  ls -la /mnt/seiscomp_staging/seiscomp_archive/{1900..2011}/VW/ 2>/dev/null
  ls -la /mnt/seiscomp_archive/{1900..2011}/VW/ 2>/dev/null
  ```
- **Recovery strategy.**
  1. Audit the pre-2012 year-dirs on staging + LT (commands above).
  2. For each stray file, identify which legitimate (station, year)
     it should have belonged to via the file's parent-directory
     path-year in the EqServer source (the path-date is authoritative
     when the trace's internal starttime is bogus).
  3. Move/merge the stray files into the correct year-subtree, OR
     just delete them if they're known-bogus pre-GPS-fix garbage.
- **Status:** NEEDS ASSESSMENT — not yet audited whether any stray
  files actually landed in pre-2012 subtrees during the pre-22443b7
  window.
- **Notes.** The fix prevents future occurrences; recovery here is
  cleanup of any historical artifacts. Low urgency vs Issues 1-3 if
  the audit comes up empty.

---

## Issue 5 — Gecko/Minimus mid-day sample-rate change crashes the merge

- **Detected:** 2026-06-03 audit. DDBE 2019-12-16 produced a Python
  exception during convert (status `error`, not `parse_error`):
  ```
  Exception: Can not merge traces with same ids (VW.DDBE.00.HHE)
    but differing sampling rates (200.0, 250.0)!
  ```
  Confirmed via mseed header probe: the rate changed mid-day (last
  file of 2019-12-16 already at 250 sps; first file of 2019-12-17 also
  at 250 sps with corrected `_CHE`-style filename).
- **Root cause.** The upstream/relay didn't update the file-naming
  convention when the recorder changed rate mid-day, so the late-day
  files carry `_HHE` in the filename despite holding 250 sps records
  inside. `convert_minimus_day` (and `convert_gecko_day` by symmetry)
  trust the source mseed channel id verbatim — both 200 sps and 250
  sps traces end up with id `VW.DDBE.00.HHE`. ObsPy `Stream.merge()`
  refuses to merge same-id different-rate traces and raises.
- **Engine gap.** The EchoPro path (`convert_echopro_day`) avoids
  this because the disk_to_sds engine's `channel_for(rate)` reassigns
  band per-trace before merge. The Gecko/Minimus paths do NOT do this
  — they should. Fix: in `convert_gecko_day` and `convert_minimus_day`,
  reassign each trace's `stats.channel` via `seed_band(rate) +
  instrument + orientation` BEFORE calling `st.merge()`. That splits
  the merge into per-band groups and writes both 200-sps `HH*` and
  250-sps `CH*` SDS files cleanly.
- **Fix landed:** none yet. Engine change belongs in disk_to_sds (the
  `seed_band` function is already there); the call site to update is
  in eqserver `scan/phase3_driver.py` `convert_gecko_day` /
  `convert_minimus_day`.
- **Affected scope.** Criterion: convert log entries with `error`
  status (NOT `parse_error`), specifically with the
  `"Can not merge traces with same ids ... but differing sampling
  rates"` message. Known: DDBE 2019-12-16 (one day). Possibly broader
  — any other Gecko/Minimus station-year with an intra-band rate
  change would crash the same way; cross-band changes (200→250) are
  the only visible class in VW so far. The post-VW Level 2 header
  scan (CLAUDE.md "Level 2 — Header scan") is the systematic way to
  identify hidden cases before DU launch.
- **Recovery strategy.**
  1. Ship the per-trace band reassignment in the gecko/minimus
     paths.
  2. Day-level retry for the known affected day (DDBE 2019-12-16).
  3. Once Level 2 has run, any newly-surfaced mid-day rate-change
     candidates from other stations get the same treatment.
- **Status:** PENDING — engine fix queued; one known affected day so
  far. Don't block VW sweep on this; the failure is one day, not a
  unit.
- **Notes.** This was the trigger for the Level 2 design expansion in
  CLAUDE.md — the intra-band rate-change class is invisible at Level 1
  and would silently silently mis-merge if Level 2 doesn't surface it
  before DU launch.

---

## Issue 6 — CLIF 2018-01-16 STEIM2 encoding spike (single day lost)

- **Detected:** 2026-06-03 audit. CLIF 2018-01-16 produced:
  ```
  InternalMSEEDError: msr_encode_steim2(VW_CLIF_00_CHN_D):
    Unable to represent difference in <= 30 bits
  ```
- **Root cause.** A sample-to-sample delta in one record exceeded
  STEIM2's 30-bit dynamic range — typically caused by a recorder
  spike or clock anomaly. The whole day failed to write because one
  bad record couldn't encode.
- **Fix landed:** none yet. Two options for handling at conversion
  time: (a) skip the offending record (drop a fraction of a second of
  data, keep the rest of the day); (b) clip the spike value into
  STEIM2's representable range. (a) is safer because (b) silently
  alters the recorded signal.
- **Affected scope.** Criterion: convert log entries with `error`
  status carrying `Unable to represent difference in <= 30 bits`.
  Known: CLIF 2018-01-16 (one day).
- **Recovery strategy.**
  1. Modify the engine to catch `InternalMSEEDError` per-record and
     skip-with-log instead of failing the day.
  2. Day-level retry for the known affected day.
- **Status:** PENDING — engine fix queued; one known day.

---

## Schema reminder for next scan

When opening `scan2_DU_recovery_register.md`, copy the file header
+ this issue-section shape. Keep the same field set:

- Detected (date + how)
- Root cause (one-liner)
- Fix landed (commit + date)
- Affected scope (criterion + snapshot)
- Recovery strategy (numbered steps with commands where useful)
- Status (PENDING / IN PROGRESS / DONE)
- Notes (anything else durable; backlinks to handoffs/memory)

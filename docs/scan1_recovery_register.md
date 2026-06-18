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

## Issue 7 — Held-queue policy decision: release 4 VW units via hard override-commit

- **Detected:** 2026-06-11. End-of-VW-sweep audit of held.jsonl found 4
  units in held state from the production sweep, none ever released:
  FORG 2024, OUTU 2024, WDSD 2025, WPSH 2024. All held with reason
  `"overrides > 0"` from `promote.py`'s dry-run gate.
- **Root cause.** Misunderstanding of what the `decide()` rule's
  "override" decision means. Apply.py's `decide()` is **strictly
  additive gap-fill** — it returns `override` only when staging has
  MORE samples than LT by more than 0.1% of a full day. It never
  returns override when staging and LT samples are equal or LT is
  larger (those are `skip`). So an override is always a *data
  recovery* — staging has samples LT doesn't. The held-queue gate
  treats any non-zero override count as needing human review, which
  is over-conservative for the gap-fill semantics.
- **Per-unit shape:**
  | Unit | write | override | skip | LT-vs-stg shape |
  |---|---|---|---|---|
  | FORG 2024 | 51 | 1 | 305 | LT 167d/c, stg 116/116/125; single-day gap-fill |
  | OUTU 2024 | 492 | 174 | 120 | LT 98d/c (partial seedlink), stg 262 — large genuine gap-fill |
  | WDSD 2025 | 552 | 3 | 0 | LT 111-112d/c (seedlink), stg 185; tiny boundary fills |
  | WPSH 2024 | 0 | 102 | 104 | LT 264d/c × 3 components (complete), stg CHZ-only 206d; eqserver source partial |
- **Fix landed (policy):** Operator authorized 2026-06-11 to
  hard-override-commit all four via `apply.py --mode decide --commit`
  (which writes the overrides per the rule). Reasoning: the override
  direction is strictly additive, mediaflux soft-delete (1yr) +
  versioned overwrites are the backstop, no actual byte-divergence
  risk in the decide-mode semantics. Holding the year for a 0.3%
  override rate (FORG) or 0.5% (WDSD) sacrifices most-of-a-year of
  promotion to protect a handful of days that the rule says we should
  extend anyway.
- **Affected scope.** Criterion: `held.jsonl` entries with
  `action=held` and `reason="overrides > 0"`. Snapshot: 4 entries
  listed above (all 4 will be released by the documented operation).
- **Recovery strategy.**
  1. Run `/tmp/release_held_units.py` on dev1 (script staged 2026-06-11):
     preflights LT writability → invokes apply.py --mode decide
     --commit per unit → appends promote_done.jsonl → rewrites
     held.jsonl with released entries removed (backup written
     alongside).
  2. Verify ledger autocommit pushed all four runs/<run_id>/run.json
     + policies/<sha>.yaml.
  3. After release, the 4 units roll into the cleanup pile with the
     other 343 promoted.
  4. Optional later: revisit the WPSH 2024 CHZ-only situation
     specifically — eqserver source has only vertical, LT has full
     3-comp from elsewhere. Worth understanding the source asymmetry
     before similar SD-card scenarios appear.
- **Status:** PENDING — script staged at `/tmp/release_held_units.py`
  on staging VM 2026-06-11 17:55Z; execution blocked on dev1
  reachability (port 22 timed out from staging VM at audit time;
  retry when dev1 is back).
- **Notes.** Tightens the held-queue policy: future production sweeps
  should release `reason="overrides > 0"` entries on operator review
  by default, treating any STRUCTURAL anomalies (e.g. WPSH-shaped
  one-component-only) as the actual review case. The gate as
  currently written is correct (any auto-commit of overrides
  warrants a human gate), but operator can sign off in bulk when the
  override pattern is recognised gap-fill. The disk_to_sds field
  history (`feedback-held-queue-is-load-bearing`) showed an override
  rule wrong in production once; this issue does NOT invalidate that
  — that case was an `--mode overwrite` blast-radius concern, not the
  `decide` semantics here.

---

## Issue 8 — KRAN accelerometer on c04-c06 (no action), SOMU Trillium on c04-c06 (silent loss, action needed)

- **Detected:** 2026-06-11. Triage of VW zero-write promotions surfaced KRAN 2012 (65 days) and SOMU 2019 (60 days) as "c04-c06-only, traces=0" — candidate Bug B (velocity wired to input B). Operator verified by opening one representative file per station in WAVES.
- **Findings:**
  - **KRAN 2012 c04-c06 = ACCELEROMETER.** Correctly dropped by the converter (`channel_exclude` for accelerometers). Not data loss. The 65 zero-write days are days where only the accelerometer recorded — no velocity present. **No action needed.**
  - **SOMU c04-c06 = long-period Trillium velocity sensor.** SOMU has a dual-sensor setup: standard Guralp CMG-1s on c01-c03 + Trillium long-period on c04-c06. The current converter drops c04-c06 as aux, silently discarding the Trillium velocity data.
- **Root cause (SOMU).** disk_to_sds's PC-SUDS converter hardcodes the Kelunji "input A=velocity, input B=aux" assumption — c01/c02/c03 mapped to Z/N/E and c04/c05/c06 unconditionally dropped. It has no awareness that input B can carry a second velocity sensor at stations with two seismometers.
- **Engine fix path (recommended).** Map SOMU c04-c06 to a **second SDS location code** (e.g. `SOMU.10.HHZ/HHN/HHE` for Trillium vs `SOMU.00.HHZ/HHN/HHE` for CMG-1s). Per-station registry annotation: `secondary_sensor: {input: c04-c06, sensor: trillium-..., location: "10"}`. The engine reads the annotation and emits both location codes on days where both are present.
- **Silent-drop scope — RESOLVED 2026-06-11 (audit result).** Walked all 951 SOMU day-dirs across all years (2014, 2015, 2016, 2018, 2019), sampled 3 files per day-dir. Distribution:
  - **c01-c03 only: 890 days** (CMG-only recording — what was promoted)
  - **c04-c06 only: 61 days** (Trillium-only — the original Bug B candidates, in 2019 only)
  - **BOTH c01-c03 AND c04-c06: ZERO days**

  CMG and Trillium were never recording simultaneously per day. The 61 Trillium days are the entire affected scope. There is no silent-drop hidden inside the promoted CMG data. Per-year breakdown:
  | Year | CMG (c01-c03) only | Trillium (c04-c06) only |
  |---|---|---|
  | 2014 | 24 | 0 |
  | 2015 | 286 | 0 |
  | 2016 | 66 | 0 |
  | 2018 | 283 | 0 |
  | 2019 | 231 | 61 |
- **Affected scope (KRAN).** Criterion: KRAN flagged-days list (65 days in 2012). No action.
- **Affected scope (SOMU).** Criterion: any SOMU day-dir with c04-c06 channel presence. Confirmed scope post-audit: **61 day-dirs in 2019 only**, all c04-c06-only. Re-convert with second-location-code engine to recover the Trillium velocity at e.g. `SOMU.10.*`. CMG-promoted days untouched (additive `--mode decide`).
- **Recovery strategy.**
  1. Land per-station `secondary_sensor` annotation in `station_registry.yaml` for SOMU.
  2. Engine change in disk_to_sds: respect `secondary_sensor` annotation; map c04-c06 to the secondary location code when present.
  3. Re-convert SOMU all years, gap-fill into LT via `apply.py --mode decide` (additive, no destructive override).
  4. After SOMU works, audit other VW stations for likely dual-sensor pattern (any station with EchoPro era + c04-c06 channels present in the source). Apply same fix.
- **Status:** POTENTIAL TODO — **not** a Phase 1 blocker. SOMU is not a key station; the 61 Trillium-only days are a small bounded scope; the CMG-promoted data is unaffected. Park this for a future engineering window when the dual-sensor-location-code generalisation has independent value (e.g. when we encounter another dual-sensor EchoPro station that DOES matter, or when DU/VX surface similar cases). At that point land annotation + engine fix together, re-convert SOMU 2019, gap-fill via `apply.py --mode decide`.
- **Notes.** This generalises the SOMU finding: the EchoPro "input B" convention was never hard-wired to accelerometer — it was operator discretion per deployment. Some stations may have used input B for an accelerometer (KRAN: confirmed), others for a second velocity sensor (SOMU: confirmed). Any future EchoPro station with c04-c06 traffic needs operator confirmation of what was wired there before convert-time channel decisions.

---

## Issue 9 — HDDL pre-2018 + MRDN post-2018-04-27 LT mis-labels (two stations, two different causes)

- **Detected:** 2026-06-11 via uom_seismic_metadata cross-check. Their report: "LT HDDL directory: 6,390 files starting 2012-01-04T11:24:33.856 UTC". Reported as a possible station-code collapse (HODL → HDDL) by our conversion pipeline.
- **Audit result (confirmation + scope reduction):**
  - **Actual LT HDDL pre-2018 content: 3 files**, not 6,390. All three are 2012 day-of-year 4 (= 2012-01-04), one per channel:
    - `/mnt/seiscomp_archive/2012/VW/HDDL/CHE.D/VW.HDDL.00.CHE.D.2012.004`
    - `/mnt/seiscomp_archive/2012/VW/HDDL/CHN.D/VW.HDDL.00.CHN.D.2012.004`
    - `/mnt/seiscomp_archive/2012/VW/HDDL/CHZ.D/VW.HDDL.00.CHZ.D.2012.004`
  - mseed header in all three: `NET=VW STA=HDDL LOC=00 CHA=CH{N,E,Z} year=2012 doy=4`.
  - Total LT HDDL: 5,841 files (3 + 5,838 post-2018). The metadata project's "6,390" figure is overstated; ask them to re-verify against the actual filesystem.
- **Source attribution — NOT this sweep:**
  - Registry has `HODL.coverage_start: 2012` and `HDDL.coverage_start: 2018` (correct).
  - EqServer source archive: `HDDL/continuous/` jumps from bogus-date dirs (1900/1970/1980/1989/1999) straight to 2018. No legitimate pre-2018 HDDL source data exists.
  - This sweep wrote HDDL only for 2018-2025 (matches our promote_done summaries exactly): 5,838 files.
  - The 3 mystery files predate our sweep and have to have come from the legacy bash+Java EqConvert pipeline (the `legacy/` directory tooling).
- **Fix.** Two clean options, both on dev1 (write-host), both pure mediaflux operations:
  1. **Rename** the 3 files to HODL: move `2012/VW/HDDL/CH{N,E,Z}.D/VW.HDDL.00.CH*.D.2012.004` → `2012/VW/HODL/CH{N,E,Z}.D/VW.HODL.00.CH*.D.2012.004`, and patch the mseed `STA` field in each file's records from `HDDL` to `HODL`. The latter is a 5-byte in-record edit per record (offset 8-12 in every 4096-byte record); needs a short Python helper.
  2. **Delete** the 3 files. Mediaflux soft-delete is 1-year recoverable per CLAUDE.md infrastructure section. Loses one day-channel of pre-2018 data, but uom_seismic_metadata says HDDL didn't exist then anyway — so the bytes were never going to be valid as HDDL.

  Option 2 is simpler and matches what the metadata project actually wants ("the LT archive should match canonical").
- **Affected scope.** Criterion: any LT file under `<YEAR>/VW/HDDL/` where year < 2018. Snapshot: 3 files (CHE/CHN/CHZ, all day-of-year 4, all 2012). Audit is mechanical: `find /mnt/seiscomp_archive/{2012..2017}/VW/HDDL -type f` returns the full set.
- **Status:** PENDING — pure LT cleanup (3 files), no conversion-pipeline change. No effect on this project's sweep going forward; registry already correct so future SD-card / DU writes will never produce HDDL pre-2018.
- **Notes.** Worth replying to the metadata project with the corrected count (3 not 6,390) and asking them to share the query that produced their figure so we can reconcile. Also a reminder: any "legacy LT artifacts" surface they uncover via future cross-checks should be triaged the same way — confirm whether THIS sweep contributed, and if not, treat as one-shot LT cleanup independent of the conversion pipeline.

### Part B — MRDN post-2018-04-27 mis-labeled bytes (EqServer ingest mistake, AMPLIFIED by this sweep)

- **Detected:** 2026-06-11 via uom_seismic_metadata cross-check. Their report: "LT MRDN > 2018-04-27 actually MARD, 2018-04-27 → 2019-01-11, ~4,145 files". Discovered alongside the HDDL claim.
- **Audit result (corrected 2026-06-11 after operator re-verification on dev1):**
  - **EqServer source `MRDN/continuous/` HAS mis-labeled data post-move:** months 01/02/03/04 of 2018 (legitimate, pre-move) PLUS month 11 of 2018 (day **20** only) AND month 01 of 2019 (day **11** only). The 2018-11 + 2019-01 data is upstream-mis-routed: the actual station was MARD by then but the telemetry-ingest pipeline filed it under MRDN.
  - **This sweep wrote it as MRDN** — faithfully converted what EqServer source said. Visible in `promote_done.jsonl`:
    - MRDN 2018: 354 files (mostly pre-move legitimate, plus 3 files from 2018-11-20)
    - MRDN 2019: 3 files (= 1 day × 3 channels, the 2019-01-11 mis-routed day)
  - **Actual mis-labeled scope: 6 LT files** (2 day-dirs × 3 channels), not 4,145. The metadata team's 4,145 is the *total* LT MRDN count, including the legitimate pre-move bulk.
  - **Time-range verification (2026-06-11):** MRDN copies are strict SUBSETS of the corresponding MARD same-day files. 2018-11-20: MRDN 00:47:08–00:48:08 (1 min, 11 KB) vs MARD 00:00:00–23:59:60 (full day, ~30 MB). 2019-01-11: MRDN 02:10:41–02:16:43 (~6 min, ~100 KB) vs MARD full day. Conclusion: **deleting the 6 MRDN copies loses zero waveform** — MARD already has the canonical full-day version of those records.
- **Source attribution.** Mis-label is at the EqServer telemetry-ingest boundary (upstream), not in our conversion. Operator confirmation 2026-06-11: "a station might have telemetered from under one station name but been added to EQ server under another station name."
- **Fix — DELETE-ONLY (verified safe).** Time-range check confirmed MRDN copies are subsets of MARD's already-complete full-day files; re-conversion is unnecessary. On dev1:
  ```
  rm /mnt/seiscomp_archive/2018/VW/MRDN/CH{E,N,Z}.D/VW.MRDN.*.CH*.D.2018.324
  rm /mnt/seiscomp_archive/2019/VW/MRDN/CH{E,N,Z}.D/VW.MRDN.*.CH*.D.2019.011
  ```
  6 files removed; zero waveform loss; mediaflux soft-delete is the 1-year safety net.
- **Affected scope.** Criterion: any LT file under `<YEAR>/VW/MRDN/` with doy > 117 in 2018 (post-move days) or any 2019 file. Snapshot: 6 files (3 channels × {2018-11-20, 2019-01-11}).
- **Status:** DELEGATED 2026-06-11 to `seiscomp_server_uom` — owns LT-cleanup operations from here. This project's involvement is complete (audit + scope + verification handed over).

### Part C — Optional long-term hardening: registry `coverage_end` annotations

The HDDL + MRDN mis-labels share a class of failure: **station X was renamed/moved on date D; bytes after D should NEVER carry the old station code.** The current `station_registry.yaml` has `coverage_start` but no `coverage_end`. Adding a `coverage_end` field — and having `phase3_driver.py` skip day-dirs past the coverage_end with a flagged log row — would catch any future mis-routing in EqServer ingest (or in DU / SD-card flows) without us having to spot it case-by-case.

- HODL: `coverage_end: 2018-04-19` (move to HDDL)
- MRDN: `coverage_end: 2018-04-27` (move to MARD)

Implementation cost: ~30 LOC in phase3_driver.py + per-station registry entries for the small number of stations that have known moves. Worth it before DU sweep launches, since DU might have similar within-property moves we don't yet know about. Track as part of pre-DU work, not blocking VW Phase 1 cleanup.

---

## Issue 10 — 27 GB residual staging after cleanup (per-file LT-mismatch preservation)

- **Detected:** 2026-06-12 at cleanup orchestrator completion.
- **What happened.** `run_production_cleanup.py --once` walked all 347 promote_done entries and invoked `cleanup.py --net <NET> --sta <STA>` per unit. cleanup.py's per-file rule: delete from staging if LT has the SAME size; otherwise keep + flag. Final result:
  - **347/347 units processed, zero failures**
  - **Staging 4.0 TB → 27 GB** (~99.3% freed)
- **What the 27 GB leftover IS — and why it's correct.** Per-file LT-mismatch preservation. The byte-divergent staged files were NOT deleted because LT bytes differ. Two recognised classes:
  - **Class A — `decide`-mode skip files** (LT had more samples than staging, so apply.py kept LT and skipped overwriting). Staging copy preserved as evidence of the divergence.
    | Station-year | Leftover | Origin |
    |---|---|---|
    | VW.FORG 2024 | 3.8 GB | 305 skip files from yesterday's held release |
    | VW.OUTU 2024 | 2.6 GB | 120 skip files from held release |
    | VW.WPSH 2024 | 2.4 GB | 104 skip files from held release |
    | VW.CRJN 2024 | 6.3 GB | per-day skips (no held entry, just decide-mode skips) |
    | VW.SOMU 2025 | 11 GB | per-day skips |
  - **Class B — small residue** in 2018-2021 (sub-100 MB per station: BRTH, MARD etc.) + 2023 (13 KB rounding) + 2026 (34 MB recent partials). Same skip-mode pattern at smaller scale.
- **Affected scope.** Criterion: any file remaining under `/mnt/seiscomp_staging/seiscomp_archive/` after cleanup.py declined to delete. Snapshot: 27 GB across ~10 stations in 2018, 2019, 2020, 2021, 2023, 2024, 2025, 2026.
- **Status:** OK / NO ACTION REQUIRED — the leftover is the SAFETY net working as designed. The data isn't "garbage in the way" — it's evidence of byte-divergent (day, channel) cells that the override-gate (or its per-file analogue) preserved for review. If we ever want to triage individual divergences, the per-station cleanup logs at `/tmp/eqserver_cleanup_logs/<run_id>.cleanup.log` list every kept file and why.
- **Notes.** Worth keeping in mind for any future SD-card promote rounds: the staging share is NOT empty post-cleanup; it has this 27 GB of legitimately-divergent residue. New uploads should write to fresh (net, sta, year) cells that don't collide.

---

## Issue 11 — Source-vs-LT gap audit (2026-06-18) — unintentional skips across VW

- **Detected:** 2026-06-18 via `scan/source_lt_gap_audit.py` (DB-based, see also pending pure-NFS-walk task #48 for stronger validation). Audit was triggered after LOYU 2019's `write=13` recovery raised the question "is the sweep silently losing data?".
- **Audit method:** Per-(net, sta, year) compares EqServer source-day count (from Level-1 station DBs, filtered `role=waveform AND exclude_reason IS NULL`) against LT day-file count (CIFS walk). Subtracts intentional skips (plan flagged_days, manifest `no_files`). Surviving gap is sub-classified by run-manifest `per_date_status`: `never_attempted` (date absent from manifest), `failed_status_*` (timeout/error/parse_error), `ok_but_zero_bytes` (silent failure — phase3 said ok but wrote nothing).
- **Caveats / limits of method:** Inherits any bug in `level1.py`'s `exclude_reason` rules (circular reasoning class). Independent NFS walker queued as task #48 for pre-DU validation. The audit also can't detect partial-day data loss (e.g. midnight-boundary loss before Option C landed) — only full-day gaps.

### Bug class A: test-run-clip (orchestrator marked complete after sub-year date range)

- **Mechanism:** Phase3's `phase3_invocation.argv` carried `start_date/end_date` shorter than full-year, but the orchestrator nonetheless appended a `convert_done` event and promote.py promoted whatever was staged. Subsequent runs saw the unit "complete" in convert_done and skipped it.
- **Detection:** Scan run_manifests for `argv.start_date != "YYYY-01-01"` or `argv.end_date != "YYYY-12-31"`.
- **Scope (verified):** Across all 339 VW manifests, **3 have clipped ranges**:
  | Unit | Asked | Days | Verdict |
  |---|---|---|---|
  | HOLS 2022 | 2022-01-01 → 2022-01-07 | 7 | leftover test run from 2026-05-31, never re-attempted |
  | HOLS 2023 | 2023-01-01 → 2023-01-07 | 7 | same |
  | LRNW 2019 | 2019-08-29 → 2019-12-31 | 125 | intentional — `run_recovery_register.py` sub-range |
- **Affected scope:** ~680 day-channels for HOLS (358 days × 2 years). LRNW 2019 sub-range is intentional and covered elsewhere.
- **Fix:** Re-convert HOLS 2022 and HOLS 2023 with full-year ranges. Use the same path as MOE re-conversion (clear convert_done + promote_done + cleanup_done entries, then run_production_convert.py --network VW --stations HOLS --year-min 2022 --year-max 2023).
- **Hardening to land before next sweep:** run_production_convert.py should refuse to mark a unit complete if `argv.end_date - argv.start_date < 0.9 * (full year)` AND no `partial_completion_reason` is set. This is a one-line guard.

### Bug class B: silent ok_but_zero_bytes clusters

- **Mechanism:** Phase3 reports `status: ok` in `per_date_status` but `bytes_written: 0`. Day was "processed" but produced no output. Cause unknown — possibly classifier-vs-converter disagreement (classifier said clean, converter found nothing convertable in source). Predates current engine pins.
- **Scope (from audit, partial — full pass still completing):**
  | Unit | Cluster | Days |
  |---|---|---|
  | KRAN 2012 | scattered | 65 |
  | DDSW 2017 | from 2017-10-04 | 16 |
  | DDWK 2017 | from 2017-10-04 (same days as DDSW) | 16 |
  | LOYU 2016 | 1 day | 1 |
- **DDSW + DDWK 2017 same-day pattern** suggests a regional / upstream telemetry event that produced ambiguous files for both stations. Worth pulling 1-2 affected day's source files to characterise.
- **Fix:** Per-day phase3 retry against engine 94ff229 (post-INT32-fallback + post-Echo) on the affected days. If still zero-bytes, capture the source for offline analysis.

### Bug class C: timeout / parse_error / error (small isolated)

- **Mechanism:** Phase3 day-job hit the SIGALRM 600s wall-clock, libmseed parse error, or generic error.
- **Scope (partial):** 1-3 days each on BRTH 2020/2021/2024, CLIF 2018, DDSW 2019/2021, DDWK 2021, FORG 2021, HOGN 2019, LOYU 2017, LRSE 2021, plus the rc=-9 history on DDNE/DDSW/LOYU/LRNW/LRWS already in [[project-engine-provenance-incident-2026-06-01]] context.
- **Fix:** Per-day retry on engine 94ff229. The INT32-fallback (88323ec) was specifically designed for the glitch-sample STEIM2 case which was a big share of these.

### Bug class D: never_attempted (recovery date range too narrow)

- **Mechanism:** Recovery script's date range was a strict subset of source coverage. Days outside the recovery range were never attempted.
- **Scope:** LRNW 2019 (4 days), LRWS 2020 (4 days), DDSW 2019 (1 day), DDNE 2017 (1 day), LOYU 2016 (1 day), LOCU 2020 (1 day), LRWS 2019 (1 day), MRDN 2018/2019 (1 day each).
- **Fix:** Targeted day-level retry with `phase3_driver.py --dates-file`.

### Headline impact (FINAL — audit completed 2026-06-18)

- **Total unintentional gap: 927 station-days** out of 64,211 total VW source days = **1.4% loss fraction**.
- **28 unit-years** with non-zero gap (out of ~339 audited).
- Subtract SOMU 2019 (60 days, already-known Issue-8 Trillium c04-c06 loss, tagged POTENTIAL TODO): **net new discovery = 867 days**.
- **HOLS 2022 + HOLS 2023 alone = 716 days = 83% of the new loss.** Both attributable to bug class A (test-run-clip) on 2026-05-31. Re-conversion recovers them cleanly.
- Remaining ~150 days split between bug class B (~98 days, KRAN 2012 + DDSW/DDWK 2017 clusters) and bug classes C/D (~50 days, scattered 1-5 day blips).
- **No systemic data loss found.** Every gap traces to one of the four documented bug classes. The 311 non-flagged unit-years in the audit are clean.

### Status

- **PENDING — pre-DU recovery work:**
  1. Re-convert HOLS 2022 + 2023 with full-year ranges (highest-impact).
  2. Per-day retry of zero-byte clusters (KRAN 2012, DDSW/DDWK 2017).
  3. Per-day retry of small timeout/error blips.
  4. Land the test-run-clip guard in `run_production_convert.py` before any future sweep.
  5. Pure-NFS-walk audit (task #48) before DU launch — independent validation that level1's exclude_reason rules aren't themselves hiding data.

---

## Issue 12 — Gecko/Minimus day-file fragmentation (~minute-boundary gaps surviving into LT)

- **Detected:** 2026-06-18 by downstream quake-fetch project. Symptoms: PhaseNet scan flooded with "fragments shorter than input samples" warnings; per-day scan time 5× normal (5 min → 25 min average), 73-min outliers on worst days.
- **Smoking-gun case:** `VW.STBK.00.CHZ.D.2022.296` — 450 separate ObsPy traces in one day-file, with 0.83–1.65 s gaps between each (median 1.084 s). Verified on staging VM 2026-06-18. Contrast: `VW.BEST.00.CHZ.D.2022.299` is 1 single contiguous trace covering the full day.
- **Root cause class:** Gecko/Minimus conversion path in `disk_to_sds/scripts/suds_convert.py` reads each per-minute `.ms.zip` file as a separate ObsPy trace and writes them without the consolidating `Stream.merge(method=1, fill_value=None)` step that the EchoPro path applies. The per-record start-time stamps from Gecko have ~1 sample-period offsets between consecutive minute files, which default merge can't bridge. EchoPro stations (BEST, HOLS, OUTU, FORG, etc.) are NOT affected because their SUDS reader merges all minute traces into one Stream before writing.
- **Affected scope:** All eqserver-converted Gecko-recorder station-days in VW. Confirmed:
  - STBK 2022-10-23 (Gecko, eqserver-converted): 450 records → 450 contigs (median 1.08 s gap)
  - BEST 2019-10-07 (Gecko era — EchoPro replaced by Gecko 2019-05 → 2020-02 per wiki): 44 records → 44 contigs (1.00 s median gap)
  - **STBK 2026-04-10 (Gecko, live SeedLink → SeisComP, NOT eqserver-converted): 2498 records → 1 contig (zero gaps).** This is the disambiguator — same recorder, different ingest path, no fragmentation. Proves the bug is in our eqserver→SDS conversion, not the Gecko hardware.
  - BEST 2025-07-19 onwards (Gecko, live SeedLink): single-trace-per-day across all probed dates.
  - Minimus path probes (DDBE/DDWB/SCM2) not yet returned — likely affected since they share the per-minute-file write architecture, but not formally confirmed.
- **Two-track recovery (both required per operator 2026-06-18):**
  1. **Engine fix in disk_to_sds**: add `stream.merge(method=1, fill_value=None)` before `write_sds` in the Gecko/Minimus paths of `suds_convert.py`. Canonical fix. Handoff required to disk_to_sds repo.
  2. **In-place msrepack sweep** over existing LT files: walks the LT archive, reads each day-file via ObsPy, applies the merge, writes back atomically. Cheap (~few hours unattended for whole VW LT), no source re-read required.
- **Sequencing:** Run #1 first (so the engine doesn't keep producing fragmented bytes during the catch-up window). Then #2 (catches up historical LT files). Both before DU launch.
- **Downstream impact resolves automatically once both tasks land** — quake-fetch's PhaseNet flooding stops, per-day scan time drops back to ~5 min.
- **Open before handoff to disk_to_sds:** confirm Minimus path has the same bug by probing one DDBE, DDWB, SCM2 day each. Downstream project offered to surface the cases.
- **Status:** RELEASED FROM HOLD 2026-06-18 after downstream provided the live-SeedLink disambiguator. Both tracks proceeding. Engine fix (#52) is handoff to disk_to_sds — disk_to_sds team owns the patch. msrepack pass (#53) is local to this project — script ready, awaits operator authorization to run on dev1 (write-host). Minimus probes still useful when they come back but no longer block the fix.
- **Acceptance test (downstream-proposed):** re-convert STBK 2022-10-23 from EqServer source, probe new SDS output with `merge(method=1)+split()`. Expected: 1 contig (or contig count reflecting only real outages, not per-record boundaries). Same diagnostic applies to msrepack output.

### Update 2026-06-18 (afternoon): the bug is TWO classes, not one

Source-file inspection (DDNE 2019-03-02 and STBK 2022-10-23, with operator validation in WAVES) revealed that what looked like one "Gecko fragmentation bug" is actually two genuinely-different phenomena:

**Class A — Gecko source-level fragmentation (REAL data loss in the source):**
- DDNE 2019 era: even within a single 1-minute Gecko source file, the recorder produced 5-6 sub-traces with 12-20 ms gaps (3-5 missing samples each at 250 Hz).
- These gaps are **present in BOTH the disk-recorded file AND the telemetered file** — they're NOT a packet-loss-in-transit artefact. Both source variants contain the same gaps with the same widths and same UTC locations.
- The gaps are visible in ObsPy as separate sub-traces; visible in WAVES at sample-level zoom as 3-5 missing samples (WAVES draws a horizontal hold-fill line that doesn't actually contain the missing data).
- Operator confirmation 2026-06-18: zoomed WAVES at 2019-03-02T00:01:44.42 UTC showed "3 suspicious identical-flat-but-not-zero samples" — confirming WAVES interpolates over real gaps.
- The LT fragmentation for these station-years is **faithful to source reality**. The samples were never recorded. No engine fix or re-conversion can bring them back.
- Multiplies across files: ~5 gaps/minute × 1440 minutes/day = ~7,200 gaps/day/channel, contributing the dominant share of the trace count we see in DDNE-2019-era LT files.

**Class B — Converter-introduced fragmentation (samples exist in source, lost in conversion):**
- STBK 2022-10-23 era: source minute files are 1-trace each (clean, ~60 sec of data per file).
- Our converter doesn't bridge the small clock offsets between consecutive minute files, so the LT day-file shows ~450 traces (one per minute file).
- Recoverable via re-conversion with the disk_to_sds engine fix (#52: add `Stream.merge(method=1, fill_value=None)` before `write_sds`).
- Confirmed by downstream's live-SeedLink probe: same Gecko recorder via SeedLink produces 1-contig day; via our archive conversion it doesn't.

**Recorder-config metadata gap (separate finding):**
For DDNE 2019-03-02, the kelunjimeta `.ss` config shows the recorder is digitising all 3 components (E/N/Z) at hardware level but the disk file only contains CHZ; the telemetered file contains all 3 (CHE/CHN/CHZ). **This is opposite to the typical norm** (disk usually has full data, telemetry is the subset). The Gecko firmware has explicit "storing channels" and "telemetered channels" configuration knobs per operator, but the **.ss does NOT explicitly record which channels are saved to disk vs telemetered** — only `tele_chan=1` whose semantics are unclear given that the file actually contains 3 channels. Means: for any Gecko station-day, you can't infer disk-vs-telemetry channel coverage from metadata alone; you have to read the file to find out.

### Decision still open (operator)

Class A is genuine source loss. Choosing whether to interpolate-at-LT (filling synthetic samples ≤ ~10 samples wide) vs preserve-honest-and-fix-downstream is a data-philosophy decision (discussed 2026-06-18 with cost measurement: ~10 sec per day-channel downstream overhead per consumer per read vs one-time ~6 days CPU to re-convert all VW). Draft email to SRC peer organisation written but not sent — asking how they handled the same class of problem in their archive conversion.

### Affected scope refinement (per-station-year)

Blast-radius sampling 2026-06-18 against Gecko-only stations (10 random files per station-year):

| Station | Class A / heavy fragmentation years | Clean / handled-well years |
|---|---|---|
| DDNE | 2017 (70%), 2018 (70%), 2019 (100%) | 2020-2024 (mostly 0%) |
| BRTH | 2018 (90%), 2019 (60%) | 2020-2024 (mostly 0%) |
| FORG | 2021 (30%) | 2017-2020, 2022-2025 |

BRTH 2025-2026 ALSO show heavy fragmentation but are out-of-scope for this pipeline (live SeedLink, not eqserver-converted; that's a different team's concern).

The transition around 2020 (from "intrinsically gappy" to "clean") is probably a recorder firmware update — worth confirming, but doesn't change recovery action.

**Per-station-year class classification is needed to decide which units to re-convert** (only Class B units benefit from the engine fix; Class A units re-convert to the same gappy result). Pending tool (planned but not built).

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

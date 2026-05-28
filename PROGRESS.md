# eqserver_2_seiscomp — Progress / Status

_Snapshot written 2026-05-28 (evening), reconstructed from the filesystem and the
staging VM after a context loss. This is a working handoff note, not a spec — see
`CLAUDE.md` for the authoritative design._

---

## TL;DR — where we are right now

- **The pipeline is built end-to-end and validated on real data, but the full
  production run has NOT been launched yet.** `/tmp/production_state.json` on the
  VM is empty; the production staging tree holds only empty year-dirs.
- **The VM is idle** (`rs-l-0ezd3a` / `172.26.144.41`) — nothing converting, no
  tmux/screen session, load ~0.3.
- Most recent work (this evening): a **preliminary 2-station commit run**
  (OUTU + STBK) succeeded, then an **event-driven QA pass** over 48 known
  earthquakes × 41 VW stations, then a **Gecko read benchmark**, then final
  edits to `run_production.py` (19:53) and `phase3_driver.py` (20:33).

## Project in one paragraph

Replace the legacy bash + Java `EqConvert` pipeline with a Python pipeline that
converts the decade-scale EqServer waveform archive into a clean SeisComP SDS
archive. Conceptually this is just **another SDS source** feeding the same
long-term archive as the sibling `disk_to_sds` (SD-card ingest) project; both
write into a shared staging SDS and promote to long-term via the shared
`sds_staging_ledger/apply.py`. Core deps: `sudspy` (PC-SUDS parsing) + ObsPy.

## Three-tier storage (all from the VM)

```
Origin (NFS, ro)            Staging (CIFS, rw, shared)        Long-term (CIFS)
/mnt/eqserver_archive   →   /mnt/seiscomp_staging         →   /mnt/seiscomp_archive
                            /seiscomp_archive                 (write only via ledger)
```

Origin is sacrosanct (read-only). Staging is per-station scratch, wiped between
stations. LT is written only through `sds_staging_ledger/apply.py` (atomic,
dry-run by default, never deletes).

---

## Components built (`scan/`, all untracked in git so far)

| File | Role |
|---|---|
| `level1.py` | Level-1 filename scanner (stdlib only, parallel per-station part-DBs) |
| `build_per_station_dbs.py` / `merge_parts.py` | Split/merge the manifest into per-station DBs |
| `check_manifest.py` | Day classifier (**v2.2**) — clean/mixed/edge/skip, recorder-aware |
| `cross_source.py` (+ `test_cross_source.py`) | Per-HHMM disk/telemetry dedup + disk-preference selection |
| `plan_generator.py` | Per-station conversion plan YAML (network/location/channel-code decision tree, review gate) |
| `phase3_driver.py` | **Per-station conversion driver** — echopro / gecko / minimus branches, parallel pool, SDS write |
| `run_production.py` | **Production orchestrator** — one station at a time, optional ledger promote + staging clear, resumable |
| `run_phase3_pool.py` | Multi-station pool runner (used for the preliminary run) |
| `metadata_harvest.py` | Empirical metadata harvest (PC-SUDS headers / Gecko `.ss`) → `uom_seismic_metadata` schema |
| `events_extract.py` | **Event-driven QA** — convert + slice ±2.5 min windows around known earthquakes |
| `gecko_read_benchmark.py` | Benchmarks in-memory-zip vs direct-ZipFile read (NFS-seek penalty) |
| `smoke_runner.py` / `smoke_matrix.yaml` / `smoke_workflow.py` | Per-recorder smoke tests (echopro/gecko/reftek/minimus/piesmo) |
| `stress_harness.py` / `profile_battery.sh` | Performance / parallelism profiling |
| `test_parser.py` | Filename-grammar parser tests |

### Key driver design points (in `phase3_driver.py`)

- **Recorder branches:** echopro (sudspy → `convert_suds_files`), gecko, minimus.
  RT130 aliases to the gecko read path (same dashed-date `.ms.zip` grammar).
- **`_concat_zip_members`:** reads each `.ms.zip` whole in one sequential NFS read,
  then opens the ZIP from memory — collapses the per-member NFS seeks (~2× faster
  cold). This is what the Gecko benchmark validated.
- **`_write_sds_retry`:** linear backoff on `OSError` because the staging SMB mount
  is `soft` (a blip surfaces as OSError, not a kernel retry). Writes are atomic
  (`.partial` → rename) so retries are safe.
- **Per-day gate, not per-station:** the plan's `flagged_days` are skipped; a
  station with `needs_review`/`BLOCKED` status still has its CLEAN days converted.
- Dry-run by default; `--commit` to write. STEIM2 encoding preserved end-to-end.

---

## What's been done on the VM

- **102 per-station DBs** built in `~/station_dbs/` (VW + DU; VX present too).
- **41 VW plans** in `/tmp/plans_vw` (plus `plans_gecko`, `plans_guralp_v2`,
  `plans_mini_2020`, `plans_piesmo_v2`).
- **QA validation suite** at `/mnt/seiscomp_staging/qa_check/` — `validation_results.json`
  (~163 KB) plus category dirs: `good`, `partial_days`, `failing_recorder`,
  `cross_source_recovery`, `sudspy_read_fails`, `events`.

### Preliminary commit run (real SDS written) — `/tmp/preliminary_run.log`

```
[  1/2] OK  OUTU   elapsed=  9679s  (~2.7 h)   echopro
[  2/2] OK  STBK   elapsed= 22466s  (~6.2 h)   gecko
pool-size=2, per-station-workers=4 → 1.43x parallel speedup
```

This is the real end-to-end proof that the production path converts real data
and writes valid staging SDS for both an EchoPro and a Gecko station.

### Event-driven QA pass — `/tmp/events_chained.log`

Converted + sliced ±2.5 min windows around **48 events** across **41 VW stations**;
records day-level vs event-window-level completeness per event (flags stations
with day data but no event-window data → recorder-transition signal). Mostly
healthy (e.g. mag-4.3 Leongatha 2024: 19/19 stations with event-window data).

**Known minor issues surfaced:**
- 4 CSV rows failed `fromisoformat` — origin_time has 2-digit fractional seconds
  (e.g. `…:23.65`); parser needs to tolerate variable fractional-second width.
- 1 event (`ga2019lqidxb`) returned phase3 `rc=1` but still produced data — worth
  a look at the stderr (was empty in the log).

### Gecko read benchmark — `/tmp/gecko_bench.log`

Ran on STBK 2019 (20 days, ~28.8 k files). Log is cut off right at the "COLD"
section header — **possibly where the API issue interrupted things** — so the
final numbers may not have been captured. Re-run on the idle VM if you want the
clean comparison; the optimization itself is already folded into the driver.

---

## Validated facts worth keeping (from CLAUDE.md, confirmed in code)

- **Recorder cohorts:** Minimus = DDBE/DDWB/SCM2 only (per-channel mseed IS the
  data, override the `single_channel` exclude). RT130 = LOYU/MOSU/SGWU/TRPU/WILU
  (registry-labelled; extension is indistinguishable from Gecko). PiesMo = DU SAA
  stations, HHZ-only telemetry stubs on EqServer (bulk data bypasses EqServer).
- **WNRO (RT130 1024-week bug) is NOT active in the EqServer archive** — verified;
  no Phase-3 converter needed.
- **Classifier headlines:** EchoPro full-year 2020 = 87.1% clean; Gecko Q1 2020 =
  100% clean.

---

## Next steps (suggested)

1. **Decide on the production launch.** Everything is in place to run
   `run_production.py --networks VW --commit` (stage only) for the 41 VW stations,
   review, then `--promote`. Recommend a small batch first (a few stations) before
   committing the whole network.
2. **Commit the `scan/` code to git** — it's the entire pipeline and is currently
   untracked on the `rewrite-suds2sds` branch.
3. **Fix the `events_extract.py` isoformat parse** (variable fractional seconds).
4. **Re-run the Gecko benchmark** to capture the numbers (log was truncated).
5. **Rotate the exposed credentials** (GitHub PAT + Mediaflux password) and scrub
   `~/.bash_history` on the VM — see security note in the handoff conversation.

---

## Phase 3 stress-testing handoff (next session)

Goal next session: **stress-test `phase3_driver.py` on stations from any network.**
State as of 2026-05-28 evening (verified on the VM):

### Databases — `~/station_dbs/` (home dir → survives reboot)

- **102 per-station DBs: 61 DU + 41 VW. No VX DBs exist yet** — so "any network"
  today means VW and DU; build VX first (`level1.py` → `build_per_station_dbs.py`)
  if VX is needed.
- Each `.db` is a **single `files` table = the Level-1 filename manifest** (no
  `station_days`/`station_intervals` tables — those are derived on the fly).
  Big: `VW.STBK.db` alone is ~5.89 M rows. `phase3_driver.py` only needs the
  `files` table, so Level-1 is sufficient to run Stage 3.
- `files` columns: `path, station, dir_year, dir_month, dir_day, recorder_type,
  source_type, role, file_year, file_month, file_day, date_mismatch, hhmm, ss,
  channel_suffix, filename_station, station_mismatch, flags, size_bytes, mtime,
  exclude_reason`.
- Scratch DBs to ignore: `/tmp/test_per_station/*.db`, `/tmp/stress/run.db`.

### Plans — all under `/tmp` (⚠ VOLATILE: a VM reboot wipes them)

Plans are **regenerable** from `DB + registry + FDSN` via `plan_generator.py`, so
if `/tmp` is empty next session, regenerate rather than panic. Current inventory:

| Dir | Count | Network | Notes |
|---|---|---|---|
| `/tmp/plans_vw` | 41 | VW | main set — **19 ok / 15 needs_review / 7 BLOCKED** |
| `/tmp/plans` | 11 | DU | |
| `/tmp/plans_piesmo_v2` | 11 | DU | PiesMo cohort (HHZ-only stubs) |
| `/tmp/plans_guralp` / `_v2` | 5 / 5 | VW | Minimus/Guralp cohort (DDBE/DDWB/SCM2) |
| `/tmp/plans_gecko` | 1 | VW | gecko cohort test |
| `/tmp/plans_mini_2020` | 1 | VW | |

Plan schema: `station, network, location, status (ok|needs_review|BLOCKED|
defer_conversion), summary{days_total,days_clean,days_flagged,pct_clean},
epochs[]{id,start,end,recorder,days,classifications{}}, flagged_days[]`.
Epochs are split on recorder/rate change (e.g. `VW.BEST` has 5 epochs 1989→2025,
echopro→gecko→echopro). Recorder-diverse targets for stress testing:
- **echopro:** most `plans_vw` `status: ok` stations
- **gecko:** `plans_gecko`, plus gecko epochs inside `plans_vw`
- **minimus:** `plans_guralp_v2` (DDBE/DDWB/SCM2)
- **reftek (via gecko path):** LOYU/MOSU/SGWU/TRPU/WILU (inside `plans_vw`)

### Stage 3 execution process

**Single station** (`scan/phase3_driver.py`):
```
python3 scan/phase3_driver.py <db> <plan.yaml> \
    --registry metadata/station_registry.yaml \
    --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
    [--commit] [--workers N] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--limit-days N]
```
- **Dry-run by default**; `--commit` writes SDS. `--limit-days` is the stress knob.
- Reads plan epochs → keeps SUPPORTED recorders `{echopro, gecko, minimus}`
  (`reftek_rt130` aliases to the gecko read path); `defer_conversion` → no-op.
- **Per-day gate, not per-station:** `flagged_days` skipped; `needs_review`/
  `BLOCKED` stations still convert their CLEAN days. So a BLOCKED station is a
  valid stress target — it just runs only its clean days.
- Each day-job (parallel `multiprocessing.Pool`, `imap_unordered`): own DB conn →
  `cross_source.select_files_for_day` (per-HHMM disk/tele dedup, disk preference
  w/ `--disk-size-floor-ratio`) → recorder branch → atomic per-channel STEIM2
  day-file to staging. `_write_sds_retry` handles soft-SMB `OSError` w/ backoff.
- Gecko/minimus reads use `_concat_zip_members` (read-whole-file + in-RAM seeks).

**Whole network** (`scan/run_production.py`): wraps phase3 one-station-at-a-time,
resumable via `/tmp/production_state.json`, `--networks VW,DU`, optional
`--promote` (calls ledger `apply.py` then clears staging). NOTE: it has **no
date-window flag** — converts all epochs per plan.

**Stress harness already present:** `scan/stress_harness.py`, `scan/smoke_matrix.yaml`,
`scan/smoke_runner.py`, `scan/run_phase3_pool.py` (multi-station pool used for the
preliminary OUTU+STBK run).

⚠ Also: `scan/phase3_driver_v2.py` is recovered VM-only scratch (older than the
live `phase3_driver.py`); `run_production.py` calls the **plain** driver. Decide
whether to keep or delete v2 next session.

### Reproducing the OUTU + STBK preliminary run

The 2026-05-28 preliminary commit run (OUTU echopro + STBK gecko, `preliminary_run.log`:
OUTU ~9,679 s, STBK ~22,466 s, 1.43× speedup) was driven by `scan/run_phase3_pool.py`
→ `scan/phase3_driver.py`. The wrapper invocation was **not persisted to disk** (run
inline via the agent's Bash tool, so not in `~/.bash_history`); only the `.log`
survived and it does not echo the date window. Reconstructed command:

```bash
# on the VM, from ~/projects/SubSurfObs/eqserver_2_seiscomp
/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3 \
  scan/run_phase3_pool.py \
    --station-dbs /home/unimelb.edu.au/dsand/station_dbs \
    --plans /tmp/plans_vw \
    --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
    --registry metadata/station_registry.yaml \
    --stations OUTU,STBK \
    --pool-size 2 --per-station-workers 4 --commit \
    --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>   # <-- the "five years"; exact dates UNKNOWN
```

- Everything except `--start-date/--end-date` is certain (from the script + log).
- **`--start-date/--end-date` is the unknown** — the "~5 years" window. The plans
  span full history (OUTU 2001→2025 / 2418 days; STBK epoch starts at the bogus
  `1900-01-01` artifact → 2024-02-13 / 2150 days), so a window was required to clip
  it. Confirm the years before re-running, else it converts full history.
- Drop `--commit` for a dry-run (no SDS writes). Full-history at `pool-size 2` was
  ~6 h wallclock; a 5-year window is proportionally less.
- Prereqs all live: scripts (VM + git), `disk_to_sds/.venv`, `~/station_dbs` DBs,
  `/tmp/plans_vw` (regenerate via `plan_generator.py` if `/tmp` was wiped), mounts.
- NOTE the STBK plan's `1900-01-01` epoch start is a date artifact — worth clamping
  to the real first-data year in `plan_generator.py` so unwindowed runs don't
  iterate ~45k empty days.

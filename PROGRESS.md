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

# eqserver_2_seiscomp

Python pipeline that converts a decade-scale EqServer waveform archive into a
clean SeisComP SDS archive. Replaces the legacy bash + Java EqConvert pipeline
in `legacy/`.

For full design notes, station/recorder taxonomy, infrastructure, and policy
decisions see [CLAUDE.md](CLAUDE.md).

## Pipeline at a glance

```
   Level 1 scan          Plan generation        Phase 3 convert      Ledger apply
  ─────────────         ─────────────────      ─────────────────    ────────────────
  level1.py    →   plan_generator.py    →    phase3_driver.py  →   apply.py
  (NFS walk)       (status gate)             (staging SDS)         (LT promotion)
       │                  │                        │                    │
       ↓                  ↓                        ↓                    ↓
  manifest.db        plans/*.yaml            /mnt/staging/...      /mnt/seiscomp_archive
```

Each step is dry-run by default; the operator reviews before committing the
next step.

## scan/ scripts

| Script | What it does |
|---|---|
| `level1.py` | Parallel NFS walk; emits per-(station, year) part-DBs and merges them |
| `merge_parts.py` | Standalone recovery merger; consumes parts safely with progressive delete |
| `check_manifest.py` | Day-level classifier (clean / partial / failing / defer) |
| `plan_generator.py` | Emits per-station plan YAML with `status: ok / defer_conversion`. Per-day classifications are descriptive QA labels, NOT skip signals — see [Plan regeneration](#plan-regeneration) |
| `regenerate_plans.sh` | Wraps `plan_generator.py` over every `~/station_dbs/<NET>.<STA>.db` for one network and lands the YAMLs in `plans/<NET>/`. Optional `-j` for parallel |
| `metadata_harvest.py` | Phase 2a — harvests PC-SUDS + Gecko `.ss` headers into per-station YAML |
| `cross_source.py` | Pure-function per-HHMM file selector (disk vs telemetry, threshold-based) |
| `phase3_driver.py` | Per-station EchoPro / Gecko / Minimus / RT130 converter, parallel workers, staging SDS write |
| `smoke_workflow.py` | End-to-end scan→plan→convert→validate across all 5 cohorts (~50 sec) |
| `smoke_matrix.yaml`, `smoke_runner.py` | Curated regression suite |
| `test_parser.py`, `test_cross_source.py` | Unit tests for the pure-function modules |
| `stress_harness.py` | Random-sampling scanner for statistical coverage |

## The merge step

`level1.py` parallelizes the NFS scan by giving each worker a different
`(station, year)` unit — there are ~586 such units across the archive. Each
worker writes its findings into its **own private SQLite file**
(`BEST__2018.sqlite`, `BRIG__2019.sqlite`, etc.). This is deliberate:

- **No write contention.** SQLite serializes writers; 16 workers fighting over
  one DB would block each other constantly. Per-worker DBs sidestep this.
- **Crash safety.** If a worker dies mid-unit, only its part-DB is half-written;
  the others are intact and the unit can be redone.

When scanning ends, you have ~586 separate `.sqlite` files, each with the same
schema (one row per file: path, station, date, recorder_type, size, etc.) but
each holding only that one (station, year) slice.

**The merge step combines those part-DBs into one queryable manifest.**

Mechanically:

```python
for part_db in part_dbs:
    ATTACH part_db AS part
    INSERT OR REPLACE INTO files SELECT * FROM part.files
    DETACH part
```

Then at the very end, it builds the composite indexes (`ix_station_dir`,
`ix_station_role`) on the single merged DB.

### Why merge instead of querying parts directly?

- **Downstream consumers want one DB.** `plan_generator`, `phase3_driver`,
  `smoke_workflow` all take a single `--db` arg. Sharding queries across 586
  files would mean unioning results in Python — slow and complicated.
- **One set of indexes.** A composite index like
  `(station, dir_year, dir_month, dir_day)` works across the whole archive once
  built. Built per-part would mean tiny indexes that don't help cross-part
  queries.
- **Disk efficiency.** SQLite has fixed per-file overhead (~50 KB of headers /
  freelists). 586 tiny files have ~30 MB of pure overhead before any data.
- **Easy backup.** One file to copy/snapshot.

### Why `INSERT OR REPLACE` instead of plain `INSERT`?

`path TEXT PRIMARY KEY` in the schema means duplicate paths would violate the
constraint and abort the merge. `OR REPLACE` makes the merge idempotent — if
the same file path somehow appears in two part-DBs (it shouldn't, but the
parallelism could in theory race on edge cases), the second one wins and the
merge proceeds.

### Why `merge_parts.py` deletes parts as it goes (`--delete-as-go` default)

Without progressive deletion, working set = parts (~50 GB) + growing final DB
(up to ~40 GB) + WAL/temp (~5 GB) = potentially 100 GB. The staging VM only
has ~70 GB free. With deletion, every successfully merged part frees its disk
before the next one starts, so peak working set stays bounded. Pass
`--keep-parts` if you want to retain them (e.g. for a parallel split by
network).

### Splitting by network

Working on per-network manifests (`vw_manifest.db`, `vx_manifest.db`,
`du_manifest.db`) is recommended over one monolithic DB because:

- Per-network DBs are much smaller (VW dominates but VX/DU are <10 GB each)
- Disk-full risk during merge is much lower
- Plan generation per network is simpler (no station-list filter needed)
- Per-network merges can run concurrently

The pattern: pre-partition the parts dir by network using the station
registry, then run separate merges. See the merge commands in
`scan/merge_parts.py --help`.

## Plan regeneration

Per-station plan YAMLs live in `plans/<NET>/<NET>.<STA>.plan.yaml`, in git.
They're regenerated whenever the classifier, plan_generator, or underlying
manifest DBs change. The Level-1 manifest is **per-station** (each is its
own SQLite DB at `~/station_dbs/<NET>.<STA>.db` on the staging VM), so
regeneration is a per-station loop.

The wrapper handles the loop, parallelism, and a status-breakdown summary:

```bash
# On the staging VM (where the DBs live):
scan/regenerate_plans.sh VW -j 4
# → plans/VW/VW.<STA>.plan.yaml × ~41
# wallclock ~15-20 min at -j 4 (PROGRESS.md baseline: ~1 h at -j 1)
```

Plan status is now `ok` or `defer_conversion` only. `defer_conversion` is
the single legitimate station-level skip (registry-annotated recorder
whose EqServer presence is a misleading partial — PiesMo HHZ-only stub).
Everything else is `ok`. Per-day classifications (`failing_recorder_disk`,
`partial_*`, `other`, etc.) remain in the plan as descriptive QA metadata
but **do not gate conversion** — phase3 attempts every day in each
epoch's range, and only runtime-detected pathology (unreadable files →
`status: parse_error` from the worker) results in no SDS output for a
day.

This is a deliberate framing: the pipeline converts what's on disc.
Recorder restarts, partial days, low-power outages, and disk-vs-tele
disagreement are operational reality of the archive, not skip signals.

## Quick smoke test

```bash
# End-to-end across all 5 recorder cohorts in ~50 sec
python3 scan/smoke_workflow.py \
    --registry metadata/station_registry.yaml \
    --staging-sds /tmp/smoke_staging \
    --scratch /tmp/smoke_scratch
```

## Convert a real station-day

```bash
# Dry-run (no SDS written)
python3 scan/phase3_driver.py vw_manifest.db plans/VW.OUTU.plan.yaml \
    --registry metadata/station_registry.yaml \
    --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
    --start-date 2020-05-27 --limit-days 1

# Commit
python3 scan/phase3_driver.py vw_manifest.db plans/VW.OUTU.plan.yaml \
    --registry metadata/station_registry.yaml \
    --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
    --start-date 2020-05-27 --limit-days 1 --commit

# After commit, the driver prints the apply.py command to promote
# staging→LT through the sds_staging_ledger. Dry-run that, review, then commit.
```

## Parallelism

`phase3_driver.py` supports per-day parallelism via `--workers N`.
Each worker opens its own SQLite connection and converts one station-day at a
time. Tune to find your NFS-IOPS ceiling — measurements so far show ~3.8×
speedup at 4 workers on EchoPro days.

## Cross-source recovery

`scan/cross_source.py` is a pure-function module that, given the disk +
telemetry candidates for one station-day, returns the file subset to convert
based on per-HHMM dedup with a configurable disk-preference threshold
(`--disk-size-floor-ratio`, default 0.8). See its docstring + the named tests
in `scan/test_cross_source.py` for the full policy.

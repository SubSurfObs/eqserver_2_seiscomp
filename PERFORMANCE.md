# Performance and efficiency log

This document is the running record of what we've measured (and what we still want
to measure) about the EqServer → SeisComP conversion pipeline. `CLAUDE.md` keeps
the *design intent* for parallelism and bottleneck candidates; this file keeps the
*numbers* that fit (or don't fit) that intent.

Every experiment is structured as:

- **Objective** — the question the experiment is meant to answer
- **Configuration** — how it's run
- **Outcome** — the answer (or **Not yet tested** with the open question stated)
- **Caveats** — anything that limits the validity of the answer

## Reference rates (headline numbers worth memorising)

| Metric | Value | Source |
|---|---|---|
| Aggregate throughput, pool=4 × ws=4, 8-station mixed | **0.45 d/s** | Exp 5 (long real run, 2026-05-28) |
| Per-day echopro single-station at ws=4 | ~5–12 s/day | Exp 7 (Round 1, in flight) |
| Per-day gecko single-station at ws=4 (post-zip-fix) | ~12–14 s/day | Exp 7 (Round 1) |
| Per-day minimus single-station at ws=4 | ~16 s/day | Exp 7 (Round 1) |
| Per-day echopro OUTU full-history baseline | 4.0 s/day | Exp 6 (preliminary, 2026-05-28) |
| Per-day gecko STBK full-history baseline (pre-zip-fix) | 10.5 s/day | Exp 6 |
| Level-1 stat-walk rate per worker | ~4,500 files/sec | Exp 1 (2026-05-25/26) |

---

## Experiments

### 1. Level-1 manifest scan throughput

- **Date:** 2026-05-25/26
- **Objective:** Establish baseline read rate of origin NFS for whole-archive
  filename scanning; determine concurrency where NFS pushes back on stat-walks.
- **Configuration:** `scan/level1.py`, stat-walk only (no file opens), up to
  7 concurrent station workers.
- **Outcome:** **~4,500 files/sec per worker**, holding across 1, 4 and 7 concurrent
  workers (per-worker rate: 3,100–5,600 f/s). **NFS stat-walk has headroom past
  7 concurrent station workers.** Load-balancing matters more than raw concurrency
  — a single straggler station can cap aggregate throughput, so the worker pool
  should queue stations largest-first.
- **Caveats:** Measures stat-walking only. Reading + decompressing has a different
  load shape; the NFS server's tolerance there is the open question (see Exp 10).

### 2. Gecko `.ms.zip` in-memory read benchmark

- **Date:** 2026-05-28
- **Objective:** Quantify the NFS-seek penalty of `zipfile.ZipFile(nfs_path)`
  (random-access seeks against the live NFS file handle) versus
  `BytesIO(open(nfs_path, "rb").read())` (one sequential NFS read, then seeks
  in RAM).
- **Configuration:** `scan/gecko_read_benchmark.py` on STBK 2019, 20 days,
  workers=1, cold NFS cache.
- **Outcome:** **~2× faster cold** with the read-whole-file + open-from-BytesIO
  pattern. Each ZIP member access via direct NFS open triggers a seek to the
  end-of-central-directory at the file tail (one NFS round-trip), then back to
  the member; reading the whole tiny `.ms.zip` (~12–70 KB) in one sequential pass
  collapses that to a single round-trip. Implemented in
  `phase3_driver.py:_concat_zip_members`, shared by the gecko + minimus branches.
  **STEIM2 preserved end-to-end** — no decode/recode.
- **Caveats:** Workers=1 isolates the read pattern. At higher workers the benefit
  shrinks because parallel readers naturally amortise NFS round-trips. See Exp 7
  for the in-production validation at workers=4.

### 3. Worker-count sweep per cohort

- **Date:** 2026-05-28 (morning, `scan/profile_battery.sh` Phase 1)
- **Objective:** Find the `--workers` knee per cohort — where adding more
  per-station day-job workers stops gaining throughput.
- **Configuration:** 10-day window × 3 cohorts (OUTU echopro, STBK gecko,
  DDBE minimus) × workers ∈ {1, 4, 8, 16}.
- **Outcome:**

  | Station | Cohort | w=1 | w=4 | w=8 | w=16 |
  |---|---|---|---|---|---|
  | OUTU | echopro | 0.03 d/s | 0.67 | 0.77 | 0.83 |
  | STBK | gecko   | 0.01 d/s | 0.53 | 0.56 | 0.62 |
  | DDBE | minimus | 0.01 d/s | 0.29 | 0.30 | 0.29 |

  - **Honest knee is between w=4 and w=8.** w=4→w=16 buys only ~25%.
  - **Minimus is flat past w=4** (DDBE shows no gain at w=8/16) — likely the
    per-channel-per-minute read pattern (~4,320 small files/day) is bound by
    something other than per-day parallelism.
  - **w=1 numbers are misleading**: the huge w=1→w=4 jump is largely cache
    warming, not real parallelism scaling.
- **Caveats:** Cold/warm-cache asymmetry was not corrected between runs. Redoing
  with explicit page-cache flush between configs would clean up the baseline,
  but the practical knee (w≈4–8) is the actionable finding regardless.

### 4. Pool grid — same total NFS readers, varied scheduling

- **Date:** 2026-05-28 (morning, `scan/profile_battery.sh` Phase 2)
- **Objective:** Decide whether to spread parallelism across stations (`pool > 1`)
  or stack workers within one station (`pool = 1`), holding total NFS readers
  constant.
- **Configuration:** 4 stations (OUTU/HOLS/NARR/WPNH) × 30 days each, total NFS
  readers = 16, configs pool × ws ∈ {1×16, 2×8, 4×4, 8×2}.
- **Outcome:**

  | pool × ws | Wallclock |
  |---|---|
  | 1 × 16 | 257 s |
  | 2 × 8  | **104 s** |
  | 4 × 4  | **102 s** |
  | 8 × 2  | 105 s |

  - **pool ≥ 2 roughly doubles throughput vs pool=1.** The gain is not from more
    NFS readers (held constant) — it's from eliminating inter-station idle time
    while one station finishes its tail. pool=2/4/8 are indistinguishable.
- **Caveats:** **pool > 1 breaks the clean per-station staging → verify → promote
  → clear boundary the ledger expects.** Production uses pool=1 via
  `run_production.py` despite the ~2× wallclock cost, because the cleanliness
  of the per-station promotion boundary is load-bearing for the ledger
  integration (see PROGRESS.md and the `sds_staging_ledger` integration proposal).

### 5. Long real conversion — production-shape reference rate

- **Date:** 2026-05-28 (morning, `scan/profile_battery.sh` Phase 3)
- **Objective:** Anchor a real "station-days per hour" production rate
  against a mixed real workload.
- **Configuration:** 8 stations (OUTU/HOLS/STBK/WDSD/DDBE/DDWB/SGWU/TRPU)
  × 100-day window × pool=4 × ws=4 via `scan/run_phase3_pool.py`.
- **Outcome:** 1,770 s wallclock, 10.1 GB written, 453 SDS files.
  **Aggregate throughput: ~0.45 d/s** (800 station-days / 1,770 s).
- **Caveats:** Run *before* the Gecko zip-read optimization (commit `2a2c831`)
  landed. Gecko stations in the cohort paid the unoptimized cost. The
  post-zip-fix aggregate rate would be somewhat higher; Round 1 (Exp 7) gives
  the updated number across a broader cohort.

### 6. Preliminary five-year OUTU + STBK production-shape run

- **Date:** 2026-05-28 (afternoon)
- **Objective:** First end-to-end production-scale validation of the
  conversion pipeline on real archive data, with `--commit` to real staging.
- **Configuration:** `scan/run_phase3_pool.py`, pool=2 × ws=4 (8 effective NFS
  readers across 2 stations), date window 2018–2023, `--commit`, output to
  `/mnt/seiscomp_staging/eqserver_preview`. Pre-Exp-2 zip-read optimization.
- **Outcome:**

  | Station | Cohort | Days | Wallclock | Per-day |
  |---|---|---|---|---|
  | OUTU | echopro | 2,418 | 9,679 s (~2.7 h) | **0.250 d/s** = 4.0 s/day |
  | STBK | gecko   | 2,150 | 22,466 s (~6.2 h) | **0.096 d/s** = 10.5 s/day |

  - Aggregate parallel speedup at pool=2: **1.43×** (32,145 s cumulative
    single-station ÷ 22,466 s wallclock).
  - **STBK gecko was ~2.6× slower per day than OUTU echopro** — the original
    signal that motivated Exp 2 (the zip-read optimization).
- **Caveats:** Pre-zip-fix. The same OUTU+STBK pair through Round 1 (Exp 7)
  gives the direct A/B comparison once STBK lands.

### 7. Round 1 random-weekly stress test

- **Date:** 2026-05-30 (in flight)
- **Objective:**
  1. Validate Phase 3 across all 26 in-window VW stations at production scale
     (1,414 day-jobs, ~104 GB estimated).
  2. Exercise all 4 recorder cohorts including the previously-unproven
     minimus and RT130-via-gecko paths under `--commit`.
  3. Measure post-zip-fix gecko throughput in a like-for-like setting
     against Exp 6.
- **Configuration:** `scan/stress_random_weeks.py`, pool=1 × workers=4 (4
  effective NFS readers, one station at a time), random 8-week selection per
  station with per-station reproducible seed, output to
  `/mnt/seiscomp_staging/stress_round1`, `--commit`.
- **Outcome (partial, 13/26 stations done at time of writing):**
  - **0.089 d/s mean per station, no failures, no quota signals**, 37 GB
    written in ~2:17 wallclock.
  - DDWB minimus: 4.07 GB / 892 s / 56 days = **0.063 d/s, 16 s/day** — first
    `--commit` of the minimus per-channel branch in production. All `ok`.
  - BRTH gecko (post-zip-fix): 2.22 GB / 713 s / 56 days = **0.079 d/s,
    12.7 s/day** — *comparable to BRIG echopro 11.5 s/day*, confirming the
    zip-read optimization closed the gecko/echopro gap.
  - 2 isolated `parse_error` blips (DDSW + DDWK, one day each, both gecko);
    all other days `ok`. To investigate post-run.
- **Caveats:** Run still in flight. Direct STBK gecko A/B against Exp 6 is
  pending (STBK runs at position 21 of 26).
- **Status:** **IN PROGRESS** — update on completion.

### 8. `python-isal` swap on echopro `.gz` decompression

- **Date:** TBD (priority 1 after Round 1)
- **Objective:** Quantify the wallclock impact of replacing stdlib `zlib`/`gzip`
  with `python-isal`'s `igzip` for sudspy's `.gz` open path. Claim: ~2–3× faster
  inflate. EchoPro is dominant cohort by data volume and gzip is on its critical path.
- **Configuration:** Install `python-isal` into the disk_to_sds venv. Modify
  sudspy's `.gz` open path to use `igzip.open` when available. Rerun the Exp 3
  OUTU worker sweep at w=4 and w=8 to isolate the effect.
- **Outcome:** **Not yet tested.** Open question: does 2-3× faster inflate
  translate to a meaningful wallclock improvement in production, or is the gzip
  step already amortised behind NFS read latency?
- **Caveats:** Drop-in replacement at the import layer; semantics identical
  (output bytes equal). Low integration risk; high upside potential.

### 9. Single-day profile breakdown

- **Date:** TBD
- **Objective:** Quantify where the per-day wallclock actually goes within
  phase3 — NFS read vs gzip inflate vs SUDS parse vs ObsPy merge vs STEIM2
  write. Determines which optimization lever is worth pulling next.
- **Configuration:** Instrument one phase3 day-job with `cProfile` or
  `time.perf_counter` around the four phases. Run on 5–10 representative days
  across cohorts.
- **Outcome:** **Not yet tested.** Open question: which phase dominates, and
  is the dominant phase the same for echopro vs gecko vs minimus?
- **Caveats:** Profile instrumentation adds overhead during the profile run;
  not for production use.

### 10. Two-VM year-partitioned parallel conversion

- **Date:** TBD (NFS whitelist request being put in; typical lead time ~1 day)
- **Objective:** Establish whether running two staging VMs in parallel, each
  writing different years (no path conflict by SDS layout), gives close to
  2× wallclock speedup, or whether origin NFS / Mediaflux CIFS server-side
  caps the gain.
- **Configuration:**
  - Provision a second staging VM with the same three mounts (NFS ro origin,
    CIFS rw staging, CIFS ro LT).
  - VM1 writes years 2023 of the in-window VW set; VM2 writes years 2024–2025.
  - Same per-station-workers (4) on each VM; one station at a time per VM.
  - Total effective NFS readers across the pair: 8.
- **Outcome:** **Not yet tested.** Open questions:
  - Does origin NFS hold up under 2 VMs × 4 workers (8 readers) vs 4 readers
    on one VM?
  - Does Mediaflux CIFS handle two concurrent clients writing to disjoint
    paths cleanly?
  - Practical expected speedup: 1.5–2× wallclock.
- **Caveats:** **Two VMs is the deliberate cap** for this experiment — past
  that, the coordination cost climbs and the marginal gain is uncertain. If
  two VMs deliver the expected speedup we'll consider whether a third is
  worth it. Per-station DBs need to be replicated to the new VM (~tens of GB,
  static; rsync from the existing VM).

### 11. Telemetry-only Stage-2 selective header scan

- **Date:** TBD (lower priority)
- **Objective:** Reduce the cost of Stage 2 (precise start/end time extraction)
  on days where disk files clearly form a complete `c01-c03` set, by skipping
  the full-file inflate-to-skip-data header scan for those files and only
  running it on the (rare) telemetry files that might contain wrong-station
  noise.
- **Configuration:** Modify the Stage 2 path to selectively bypass
  `scan_suds_file` for files passing a "disk + complete c-set" heuristic
  based on Level 1 manifest data alone.
- **Outcome:** **Not yet tested.** Open question: how much wallclock does
  the current full-Stage-2 path spend on inflate-to-skip-data for files
  that trivially already have full channel coverage?
- **Caveats:** Pure design idea today (see CLAUDE.md "Decompression is the
  dominant per-file cost"). No scaffolding in place yet.

---

## Pending experiments — priority order

| # | Experiment | Why this priority |
|---|---|---|
| 8 | `python-isal` swap | Biggest single-machine echopro win plausibly available; ~1 hour to test |
| 9 | Single-day profile breakdown | Cheap; tells us whether isal is the right lever or something else is |
| 10 | Two-VM year-partitioned | Highest potential wallclock speedup; ~1 day IT lead time, then ready |
| 11 | Telemetry-only Stage-2 | Only matters at the margin for already-clean days |

## Raw artefacts

- `scan/profile_battery.sh` — Exp 3, 4, 5 (the May 28 battery). Output preserved on
  the staging VM at `/tmp/profile_results/` (`RESULTS.csv`, `SUMMARY.md`,
  per-test `.log`). **Not in git** — should be copied into the repo if we want
  the raw record durable.
- `scan/gecko_read_benchmark.py` — Exp 2.
- `scan/stress_random_weeks.py` — Exp 7.

## How to add a new experiment

1. Pick the next number.
2. Write **Objective** (the question) and **Configuration** (how you'll answer it).
3. Mark **Outcome: Not yet tested** with the open question stated.
4. Once run, fill in **Outcome** with the numbers and a one-paragraph interpretation,
   plus **Caveats** (anything that limits the validity).
5. Update the "Pending experiments" priority table.
6. Commit.

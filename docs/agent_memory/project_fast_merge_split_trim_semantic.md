---
name: project-fast-merge-split-trim-semantic
description: "Pipeline as of 2026-06-23 uses sudspy.fast_merge_split(overlap='trim') in all engines, replacing obspy.merge(method=1)+split(). Production wins are RELIABILITY not raw speed: ~18% SLOWER per-day on typical clean production days (verified 2026-06-24 on v2 BEST run), but eliminates O(N^2) timeout class on pathological days that previously failed. Yesterday's 7x catalogue claim came from cherry-picked pathological days + avoided timeouts, NOT per-day speedup. Semantic difference: trim drops overlap from later trace (preserves real measurements); obspy averaged. 0.006-0.04% of samples in scan-1 LT will diverge -- expected, not a regression."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Adopted 2026-06-23 commits:
- sudspy 20c48b4 (fast_merge_split)
- eqserver_2_seiscomp 2ade29e (phase3_driver gecko + minimus engines)
- disk_to_sds 20a483c (write_sds)

## Effect — corrected 2026-06-24 against v2 BEST production

Test catalogue claim (yesterday): 7x speedup (1,841 s vs 12,908 s on
224-day catalogue). This number is REAL but specific to that catalogue
and does NOT generalise to per-day production speed.

Production reality (v2 BEST, 5 completed years, same item counts as v1):

| Year | v1 wall | v2 wall | Ratio v1/v2 |
|---|---|---|---|
| 2024 | 1,445 s | 2,899 s | 0.50 |
| 2023 | 2,567 s | 3,614 s | 0.71 |
| 2022 | 3,969 s | 4,788 s | 0.83 |
| 2021 | 2,030 s | 2,180 s | 0.93 |
| 2020 | 3,026 s | 2,700 s | 1.12 |

Average ratio across matched-items years: ~0.85 — v2 is roughly **18%
SLOWER per-day** than v1 on typical clean production days.

Where the catalogue 7x came from:
1. Catalogue cherry-picked pathological days (severely fragmented,
   multi-source disagreement) where fast_merge_split's O(N log N) vs
   O(N^2) win is dramatic.
2. v1 had 1-3 timeouts per catalogue sweep at >=600s each. v2 has 0.
   Eliminating these is the bulk of the 7x.
3. Per-day, on clean days, fast_merge_split adds modest overhead
   (trace bookkeeping, overlap detection).

## What v2 actually wins

- **Reliability:** no day-job hangs at 600s+ timeout. No need for
  per-day-retry mechanisms. `convert_failed.jsonl` entries should drop
  to near zero on a real sweep.
- **Pathological-day coverage:** days that v1 timed out and skipped
  are now converted normally (the LRWS 2020 timeout class is fixed).
- **Output: structurally equivalent** (same trace count, same total
  sample count) to obspy.merge but semantically divergent on
  overlapping samples (see semantic difference section below).

## What v2 does NOT win

- **NOT per-day faster on typical production days.** Don't claim or
  imply a speedup that won't materialize in real sweep wall-clock.
- **NOT a full-sweep speedup either** — based on BEST's 5 years, total
  wall is comparable to or slightly longer than v1.

## Semantic difference vs scan-1 LT

Where two source traces overlap in time (sub-second clock drift at minute
boundaries, or disk-vs-tele duplicates at file boundaries):

| Old engine (obspy.merge method=1) | New engine (fast_merge_split overlap=trim) |
|---|---|
| Averages overlapping samples: outputs (X+Y)/2 | Drops Y, keeps X (the earlier-starttime trace) |
| Fabricates a value no recorder produced | Preserves a real physical measurement |

Measured impact on three differing-md5 days:
- BRTH 2018.171 CHE: 0.006% of samples differ, max abs ~8440 counts
- LRSE 2022.105 CHN: 0.012% differ, max abs ~2790 counts
- DDNE 2018.037 CHZ: 0.040% differ, max abs ~5668 counts

These differences are at trace boundaries only. The bulk of samples are
identical. Coverage and trace structure are unchanged.

## Why we picked trim

Operator decision 2026-06-23: averaging is more comfortable, trim is more
honest. Trim preserves real measurements rather than fabricating values
that no recorder ever produced. The reliability win (no timeouts on
pathological days) is the load-bearing benefit; per-day speed is
roughly comparable to slightly slower.

## Implication for LT compatibility

Scan-1 LT was written with obspy.merge(method=1) averaging. The new
engine will produce slightly different bytes at trace boundaries for
~33% of catalogue day-files (verified empirically in
test_env_classb/logs/md5_diff_staging.py output 2026-06-23). This is:

- NOT a bug or regression
- NOT a sample-count or coverage change
- A documented one-time semantic shift

After re-conversion of affected station-years, LT transitions to the
trim semantic. Future rescan-vs-LT byte comparisons stabilize at near-
zero diff.

## How to apply

- Don't try to add an `overlap=average` mode to "fix" the LT divergence —
  the user explicitly preferred trim for source fidelity AND for speed.
- When comparing rescan output to scan-1 LT, expect ~30% of day-files to
  have BYTES_DIFFER verdict but with same n_samples and same n_traces.
  That's the trim/average semantic, not contamination.
- New byte-equality regression tests should compare rescan-vs-rescan,
  not rescan-vs-scan-1-LT.

Linked: [[project-class-a-vs-class-b-framing]] (related learning about
how the "Class B / merge bug" framing was misdirection — fast_merge_split's
adoption is the actual merge-performance win, not a correctness fix).

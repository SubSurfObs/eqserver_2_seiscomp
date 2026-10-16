---
name: project-underscore-tele-files
description: "1.69M tele MSEED files use UNDERSCORE between time and station code (e.g. '2021-06-20 2355_DDWB.ms.zip'). Concentrated 97% at RT130 stations (SGWU 995k, TRPU 550k, LOYU 110k) with a small Minimus tail (DDWB 31k). Verified 2026-06-22 by per-station count across all 41 VW. DDBE/SCM2 (Minimus) have ZERO — pattern is NOT Minimus-universal."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Surfaced 2026-06-21 by `scan/investigate_unmatched.py`. The largest single
class of unmatched filenames across VW:

```
shape           D4-D2-D2 D4_A4.l2.l3                count = 1,686,872
example         "2021-06-20 2355_DDWB.ms.zip"
```

These are space-separated for date/time but **UNDERSCORE-separated between
time (HHMM) and station code** — a hybrid pattern not in CLAUDE.md.

**Why this matters** (operator stance 2026-06-21):
- CLAUDE.md establishes the project's primary file-source discriminator:
  - Disk-recorded files: underscores throughout (`20221023_0001_STBK.ms.zip`)
  - Telemetry-arrived files: spaces throughout (`2022-10-23 0001 00 STBK.ms.zip`)
- These 1.7M files violate that rule by mixing spaces and underscores
  in the same filename.
- Until we understand which they are (disk or telemetry), the
  cross_source selector cannot be trusted on any day that includes them.

**Where they cluster** (2026-06-22 per-station count across all 41 VW):

| Station | Count | Cohort |
|---|---:|---|
| SGWU | 995,378 | RT130 |
| TRPU | 550,035 | RT130 |
| LOYU | 110,308 | RT130 |
| DDWB |  31,149 | Minimus |
| BRTH |       2 | Gecko (noise) |
| **Total** | **1,686,872** | — |

- **97% are at the 5 VW RT130 stations** — and DDWB (Minimus). BRTH is
  noise (2 files). DDBE and SCM2 (also Minimus, also borehole) have
  ZERO underscore-tele — pattern is NOT Minimus-universal.
- MOSU and WILU (RT130) didn't appear in the count — confirm they have
  zero (might just be low-data stations).

**Hypotheses to test:**
- They may be **manually uploaded mseed files** for Minimus stations
  (per CLAUDE.md note about Minimus stations not being telemetered —
  Radian outputs too much data). The space-format may be operator-typed
  while the underscore-before-STA is a different upload tool's convention.
- They may be a SECOND telemetry path that we haven't documented.
- They may be operator-cleaned re-uploads of broken originals.

**How to apply:**
- Open headers of 3-5 sample files (different stations, different years).
  Compare to known disk + tele variants at the same minute.
- Identify which discriminator (source_type) they should map to.
- Add the regex pattern to `scan/level1.py` with the right source_type
  tag.
- Re-run `categorize_source.py` on affected stations and see how their
  day-shape histograms shift.
- These files are likely in scope for the second conversion attempt;
  ignoring them risks dropping substantial data at Minimus stations.

**Two more mixed-separator shapes above the 10K floor** (network-wide,
verified 2026-06-22):

| Shape | Decoded | Count | Where |
|---|---|---:|---|
| `D4-D2-D2_D4-A4` | `YYYY-MM-DD_HHMM-STA.ms.zip` | 29,243 | **WPSH** (Wilson's Prom, Gecko — NOT RT130/Minimus!) |
| `D4-D2-D2-D4_A4` | `YYYY-MM-DD-HHMM_STA.ms.zip` | 10,080 | SGWU (5,760), TRPU (4,320) — RT130 cohort |

WPSH carrying 29K files of an undocumented mixed-format shape is the
surprise — it's a Gecko station, not part of the known RT130/Minimus
ingest-path cohorts. Could be (a) an undocumented Gecko upload tool,
(b) a manual re-upload campaign, (c) an undiscovered scan 1 silent
drop. Worth a per-day source-vs-LT audit on WPSH before scan 2 to
quantify whether scan 1 ignored data here.

The level1.py grammar extension (Step 3 of [[reference-scan2-build-
order]]) thus needs THREE new patterns, not one:

- `^\d{4}-\d{2}-\d{2} \d{4}_\w+\.ms\.zip$` (underscore-tele)
- `^\d{4}-\d{2}-\d{2}_\d{4}-\w+\.ms\.zip$` (WPSH shape)
- `^\d{4}-\d{2}-\d{2}-\d{4}_\w+\.ms\.zip$` (RT130 minor shape)

Linked: [[reference-source-stats-tooling]] (the discovery tool),
[[project-eqserver-telemetry-only-via-network]] (the project's
documented telemetry path model that this pattern violates),
[[reference-scan2-build-order]] (Step 3 owns the level1 extension).

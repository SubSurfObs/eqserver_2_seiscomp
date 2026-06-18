# Handoff to disk_to_sds — Gecko/Minimus day-file fragmentation in suds_convert.py

**From:** eqserver_2_seiscomp (Dan)
**Date:** 2026-06-18
**Priority:** High — affects downstream consumers (PhaseNet scan 5× slowdown, fragmentation warnings flood); blocks DU sweep launch.

## Problem (one paragraph)

`disk_to_sds/scripts/suds_convert.py`'s Gecko (and almost certainly Minimus) day-file write path produces fragmented SDS day-files: hundreds of separate ObsPy traces with ~1 s gaps between each, instead of one continuous trace per day-channel. The EchoPro path doesn't have the bug. Root cause is almost certainly a missing `Stream.merge(method=1, fill_value=None)` (or equivalent timestamp-tolerance merge) before the final `write_sds` call. Each Gecko per-minute `.ms.zip` carries a slightly-off start-time metadata stamp; the writer is honouring that literal stamp instead of consolidating against the preceding file's end.

## Evidence

Downstream quake-fetch project found the smoking gun. All files probed on the staging VM (`/mnt/seiscomp_archive/...`):

| File | Path | Records | Contigs (after merge+split) | Median gap |
|---|---|---|---|---|
| **STBK 2022-10-23** (eqserver-converted Gecko) | `2022/VW/STBK/CHZ.D/VW.STBK.00.CHZ.D.2022.296` | 450 | **450** | **1.084 s** |
| **BEST 2019-10-07** (eqserver-converted Gecko era; BEST was on Gecko 2019-05 → 2020-02 per wiki) | `2019/VW/BEST/CHZ.D/VW.BEST.00.CHZ.D.2019.280` | 44 | **44** | **1.00 s** |
| **STBK 2026-04-10** (live SeedLink → SeisComP, NOT eqserver-converted) | `2026/VW/STBK/...` | 2498 | **1** | 0 s |
| **BEST 2025-07-19** onwards (live SeedLink Gecko) | `2025/VW/BEST/...` | varied | **1** per day | 0 s |
| **BEST 2022-10-26** (eqserver-converted EchoPro era) | `2022/VW/BEST/CHZ.D/VW.BEST.00.CHZ.D.2022.299` | 1 | 1 | n/a |

The disambiguator is STBK 2026-04-10: **same recorder (Gecko) and same station (STBK)** as the fragmented case, but the live-SeedLink ingest path produced 1 contig from 2498 records. The Gecko hardware is fine. Our eqserver → SDS conversion is introducing the gaps.

EchoPro-path files (e.g. BEST 2022-10-26) are clean because the SUDS reader produces a single in-memory Stream that's merged before writing.

## Practical impact on downstream

- quake-fetch's SDS client scan-mode explodes per-day trace count from ~80 (one per station-channel) to ~11,500 (one per record).
- PhaseNet floods the log with "fragments shorter than input samples — output might be empty" warnings.
- Per-day scan time goes from ~5 min to ~25 min average, with 73-min outliers on worst days.
- All Gecko-recorder station-days in VW are affected (BRTH, BRIG, DDNE, MARD, STBK, WLSH, WPNH, WPSH, etc.). VX Gecko stations affected (the recent MOE re-conversion bytes). Will affect every future DU sweep and SD-card upload unless fixed.

## Exact code location (best guess from outside)

`disk_to_sds/scripts/suds_convert.py`, in the Gecko write path (and probably the Minimus path by analogy). The day-file write currently looks like (paraphrased — confirm against actual source):

```python
# Per-minute files read into a Stream:
day_stream = Stream()
for minute_file in minute_files:
    day_stream += read(minute_file)
# Direct write — no consolidating merge
write_sds(day_stream, day_path)
```

EchoPro path's stream comes from the SUDS reader which produces a single trace per channel naturally, so no merge needed there. The Gecko path inherits one trace PER minute-file, and the small clock offsets between minute-file headers stop the default merge from consolidating them.

## Proposed patch (minimal)

```python
# Per-minute files read into a Stream:
day_stream = Stream()
for minute_file in minute_files:
    day_stream += read(minute_file)

# Consolidate small inter-file timestamp offsets into one continuous trace
# per channel before SDS write. method=1 handles slight overlaps / sub-sample
# gaps; fill_value=None preserves real gaps (e.g. minutes-long outages) as
# masked, so they don't get silently filled.
day_stream.merge(method=1, fill_value=None)

write_sds(day_stream, day_path)
```

That's it. The EchoPro path is unaffected (already produces one trace per channel). The Minimus path almost certainly has the same bug — if it has the same per-channel-per-minute file architecture, apply the same fix in its write path.

## Acceptance test (downstream-proposed)

After patch:
1. Re-convert VW.STBK 2022-10-23 from EqServer source files via the patched engine.
2. Read the resulting SDS day-file with ObsPy.
3. Call `stream.merge(method=1)` then `split()`.
4. Expected: **1 contiguous trace** (or N contigs reflecting only real outages, not per-record boundaries).

This same diagnostic is what we'll run on the in-place msrepack output to verify the parallel recovery path also works.

## Cross-repo coordination after the patch lands

eqserver_2_seiscomp will:

1. Pin the new engine SHA in `CLAUDE.md` "Shared conversion core" section.
2. Re-convert affected VX bytes from this morning's MOE round (they're in the new VX LT and will need re-doing). The HOLS 2022/2023 re-conversion (task #49) and per-day zero-byte retries (task #51) all benefit from the fix being live before they run.
3. The in-place msrepack sweep over existing LT files (task #53) is the parallel recovery path — it doesn't depend on the patch shipping, but its output needs to match the patched engine's output. We'll run it on dev1 once we have a green light.

## What we still need (small)

If you can verify whether the Minimus path needs the same fix — i.e. does `convert_minimus_day` (or its equivalent) build the day stream from per-channel-per-minute files and write without merging — that would close the last open scope question. If yes, apply the same patch there. If the Minimus path uses a different architecture, let us know what it looks like.

## Reply with

- New SHA
- Brief notes on whether the Minimus path is affected
- Confirmation that the acceptance test passes (or what showed up if it didn't)

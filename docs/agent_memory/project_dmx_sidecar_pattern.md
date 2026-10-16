---
name: project-dmx-sidecar-pattern
description: EqServer ingest path produces single-channel SUDS sidecar files (1-channel UM.<STA>.CHZ) alongside Gecko 3-channel mseed. 19 stations × 56 station-years affected network-wide. plan_generator.manifest_recorder protected against the dispatch contamination 2026-06-22.
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Investigation 2026-06-22. While auditing channel_epoch_scan results, BEST
appeared to show `tele_ss_suds: c03 > CHZ > c03` — looks like a recorder
type-inversion bug but turned out to be REAL DATA from an EqServer ingest
artifact.

## The pattern

EqServer (or its predecessor pipeline) appears to wrap a copy of each
Gecko mseed's Z channel into a SUDS-format `.dmx` file. These files:

- Are named like Gecko telemetry: `YYYY-MM-DD HHMM SS BEST.dmx`
- Contain ONE channel only: `UM.<STA>.CHZ` (note: `UM` network
  placeholder = Gecko default, NOT `AB.BEST.c03` EchoPro default)
- Carry SEED-style naming (CHZ at 250 Hz; CHZ at 100/200 Hz would be HHZ)
- Are sized like a 60-second full-channel waveform (~55-67 KB)

So they're real waveform data, but it's the SAME Z trace that's already
in the Gecko's 3-channel `.ms.zip` at the same minute. Dedup target.

## Network-wide reach (verified 2026-06-22)

19 stations × 56 station-years show coexistence of `recorder_type='echopro'`
AND `recorder_type='gecko'` in the manifest. Sample probe results:

- **Pure sidecar (1-channel `UM.<STA>.CHZ`)**: BEST, FORG, CLIF
- **Only `.trig.dmx` triggered events, no continuous `.dmx`**: BRTH, WDSD,
  DEAL, HOGN, SCMB, SGWU, TRPU, DDNE, DDWK, DDSW. NOT a real EchoPro at
  these stations — the level1 `echopro` tag is just from triggered files.
- **Genuine WNRO-recovered EchoPro**: LOCU 2021-07 + NARR 2021-07
  (`.wrno.dmx.gz` with `AB.<STA>.c01/c02/c03` — 3-channel real recordings,
  timing-corrected). Distinct phenomenon from sidecars.

## The risk and the fix

`plan_generator.manifest_recorder` previously picked
`max(n_echopro_ok, n_gecko_ok, n_mseed_ok)`. On days where `.dmx` sidecar
count exceeds `.ms.zip` count (Gecko sparse + sidecar normal), this
routed conversion to `convert_echopro_day`, producing Z-only SDS output
and silently losing CHN/CHE that ARE in the `.ms.zip`.

Fix (commit d43be93): when `n_gecko_ok >= 100` OR `>= 5% of n_echopro_ok`,
gecko wins regardless of file-count comparison. Same protection for
Minimus (`n_mseed_ok >= 2000`). The widespread sidecar pattern means
coexistence is the rule, not real dual-recorder operation.

## What's still pending

1. **Registry recorder_types backfill** (task #55): the derive-from-DB
   inference is contaminated by the sidecar pattern (BRTH/WDSD/DEAL show
   "echopro" inferred but they're Gecko-only with `.trig.dmx`). A proper
   backfill needs to look at file CONTENTS, not just `recorder_type` tags.
   Derived inference is saved at `/home/.../test_env_classb/logs/
   recorder_types_derived.tsv` for reference.
2. **channel_epoch_scan velocity filter** keeps SEED codes for the
   sidecar files. The `tele_*_suds` epochs for sidecar-affected stations
   will show CHZ during Gecko era — this is REAL data the scanner
   reported, NOT a scanner bug. coverage_planner (Step 4) will see
   `tele_*_suds` has only `[CHZ]` while `tele_*_mseed` has `[CHE,CHN,CHZ]`
   and pick mseed via the existing priority logic.
3. **Source priority in plan_generator** (Step 3): when source_priority
   per channel is consulted, the priority list (commit 16f039f) already
   places `disk_mseed > disk_suds`. The sidecar protection at the
   dispatch layer (manifest_recorder) is belt-and-braces for the case
   where Step 4 coverage_planner isn't yet active.

Related: [[project-class-a-vs-class-b-framing]] (the other widespread
pipeline-side problem class), [[reference-scan2-build-order]].

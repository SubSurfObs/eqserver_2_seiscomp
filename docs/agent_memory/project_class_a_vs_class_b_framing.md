---
name: project-class-a-vs-class-b-framing
description: "Class A = real EqServer source-level data gaps (irrecoverable). Class B = a class of pipeline-side problems producing fragmented LT day-files (multiple sub-mechanisms B1, B2, etc — still being scoped 2026-06-20)."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

For the eqserver → SDS conversion, problem classes:

**Class A — real source-level gaps.**
Genuine recorder-level data loss baked into the EqServer source bytes. No engine fix can recover them. Confirmed seen at DDNE 2019 (Gecko source has ~12-20 ms gaps within minute files, ~3-5 missing samples each at 250 Hz). Whether the same exists in EchoPro or other recorder source is not confirmed and should not be claimed without evidence. We document Class A days, leave them gapped, move on.

**Class B — a class of pipeline-side problems.** Not a single bug. The shared symptom is "fragmented LT day-files" (hundreds of traces in a single SDS day-channel file instead of 1). Identified sub-mechanisms:
- **B1**: engine-layer merge / write_sds bug. disk_to_sds team identified and fixed; verified by smoke test on clean-input (2 disk files for STBK 2022-10-23) producing 1 clean trace.
- **B2**: cross_source.select_files_for_day mixes the two telemetry variants (`ss=00` continuous-session vs no-SS) without using the SS field as a discriminator. When mixed, the merged stream has artificial overlap+gap patterns that ObsPy masks and split() fragments. Verified in notebook `notebooks/stbk_2022_10_23_overlap_diagnosis.ipynb` 2026-06-20.
- **B?**: at least one more mechanism suspected — LRSE 2022 came out at 57% sample completeness in the test suite, more than fragmentation alone explains. Not yet diagnosed.

**The LT trace-count scan (2026-06-19)** found 136 station-years with the fragmentation fingerprint. This was attributed to the "side-load window" (2026-06-01 → 2026-06-12 engine pin issue), but **that attribution turned out to be wrong** — empirical verification 2026-06-23:

- **BRTH 2020-12-18 CHZ** flagged with **33,545 traces** in LT (extreme fragmentation, max in the network-wide scan): directly verified against EqServer source. Source contains 39,385 raw CHZ traces; after `obspy.merge + split` of the source, result is **33,557 traces** — virtually identical to LT. **LT is correct**; the fragmentation is real Gecko-firmware packet structure (412-sample = 1.648s packets with 140-280ms inter-packet gaps).
- **DDBE 2019-10-02 / SCM2 2019-11-07** (Minimus M_perchan, marked "side-load affected" by the audit, LT mtime in window): LT and rescan **byte-for-byte identical**, 1 trace per channel — no contamination.
- **DDNE 2018-05-31** (Gecko clean, marked affected): LT and rescan byte-for-byte identical.

The lt_trace_count_scan's "CLASS-B-LIKELY" heuristic (>10 traces = heavy, >100 = definite) over-flags Gecko stations whose recorder firmware writes sub-second packets. The 136 flags should be re-examined per-station-year — many are Class A in disguise. Provisional estimate: task #54 (Class B re-conversion) may be entirely unnecessary; spot-checking 5-6 random affected station-years against source confirmed all were Class A so far.

**How to apply:**
- Do not use "Class B" as if it's a single bug. It's a bucket of suspected pipeline issues.
- Before saying "Class B is fixed" or "Class B affects X," specify which sub-mechanism (B1 / B2 / etc).
- Before re-running production conversion, scope each sub-mechanism independently — fix B2 only after measuring its blast radius, not based on the side-load window framing.
- The 95%-done-on-six-month-deadline framing applies; bounded scoping work, not a panic.

Linked: [[feedback-verify-before-claiming]], [[feedback-side-load-blast-radius-is-full-sweep]], [[project-class-b-affects-echopro-too]].

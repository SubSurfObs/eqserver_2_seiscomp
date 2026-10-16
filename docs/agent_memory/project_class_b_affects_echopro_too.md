---
name: project-class-b-affects-echopro-too
description: "Class B (missing stream.merge before write_sds) affects EchoPro days too, not just Gecko/Minimus. LT trace-count scan confirmed 2026-06-19: 136 Class B candidates including pure-EchoPro station-years (BEST, HOLS, CLIF, MRDN, NARR, HODL, KRAN, MARD, WPNH, LOCU)."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

The Class B bug (missing `stream.merge(method=1, fill_value=None)` before `write_sds`) is a property of the WRITE path, not the read path. So any recorder type that produces multiple traces per day in the ObsPy Stream at write time is affected. That includes EchoPro.

**Why:** the missing merge operates on whatever traces are in the Stream. For Gecko/Minimus that's 1440 per-minute files → 1440+ traces per day → hundreds of records in the SDS day file. For EchoPro it's session-based — fewer files but still multiple traces per day across session boundaries and recorder restarts. The fragmentation is less extreme but still real.

**Verified 2026-06-19 by direct LT trace-count scan:**
- BEST 2017 (pure EchoPro per station DB): 21 definite-fragmented files (>100 traces each), max trace count 444
- HOLS 2017-2021 (pure EchoPro): flagged
- CLIF, MRDN, NARR, HODL, KRAN, MARD, WPNH, LOCU multiple years: flagged
- All converted during the side-load window 2026-06-01 → 2026-06-12

**Why:** I previously claimed "EchoPro takes a different code path and is structurally unaffected." That was wrong. The path-divergence I was thinking of is the READ side (SUDS reader vs Gecko zip reader). Both paths converge at `write_sds`, which is where the bug lives.

**Why:** Blast radius for Class B re-conversion is closer to the FULL 320 station-years in the side-load window, not the ~115 Gecko/Minimus/RT130 subset I'd been narrowing to. Scope ~3× larger than I'd estimated.

**How to apply:**

When discussing Class B re-conversion scope or blast radius:
- Do NOT exclude EchoPro station-years from the candidate set.
- Treat the side-load window (2026-06-01 → 2026-06-12) as the binding constraint, not the recorder type.
- The 136 LT trace-count candidates are a LOWER bound (the scan's threshold is `definite ≥ 5 OR definite/total > 0.5`); mild fragmentation may slip under.
- For full safety, treat ALL 320 in-window station-years as Class B re-conversion candidates.

Linked: [[feedback-side-load-blast-radius-is-full-sweep]], [[project-engine-provenance-incident-2026-06-01]].

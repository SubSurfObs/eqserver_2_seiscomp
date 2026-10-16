---
name: project-wrno-files
description: .wrno.dmx.gz files (~970k network-wide) carry a WNRO (GPS Week Number RollOver) tag — files whose original GPS timestamps were affected by the 1024-week rollover bug. Filename dates are uncorrected (often appear as 1989/1999). Real recording date = filename date + 19.7 years × N. Already addressed by ingest-side correction per CLAUDE.md.
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Surfaced 2026-06-21 by `scan/investigate_unmatched.py`: ~970,000 files
across VW with the shape `YYYY-MM-DD HHMM SS STA.wrno.dmx.gz`, dates
mostly 1989-08-17 (rollover-1) and a smaller cohort around 1999.

**WNRO = GPS Week Number RollOver**, per operator 2026-06-21:

> GPS satellites transmit a 10-bit binary counter representing the GPS
> Week Number. Every 19.7 years this counter rolls back to zero. The
> rollover event requires testing and validation in receiver firmware.
> Non-compliant receivers experience date/time errors.
> - First rollover: 1999
> - Second rollover: 2019-04-06 GPS midnight

CLAUDE.md already discusses WNRO in the Reftek RT130 context — the raw
RT130 stream was affected by the rollover, and the EqServer ingest
pipeline applied a correction so files land in correct year-dirs in the
archive. The `.wrno.dmx.gz` files are the **tagged artefacts of that
correction process** — files where the uncorrected GPS time would have
landed in a bogus year, but the dispatcher kept them in their original
(bogus) name for traceability.

**Real recording date** = filename date + 1024 weeks × N (≈ 19.7 years
per rollover). So a file named `1989-08-17` is most likely the
2009-04-something recording (1 rollover) or 2029 (2 rollovers).

**How to apply:**

- These files MAY carry real seismic data — they're the
  rollover-corrected outputs, not junk.
- Before deciding to convert or exclude:
  1. Cross-reference with [[reference-source-stats-tooling]] CLAUDE.md
     "RT130 WNRO bug" section — the user verified 2026-05-27 that header
     timestamps in EqServer archive already match their path dates for a
     sample of RT130 stations. So the correction works for those.
  2. For `.wrno.dmx.gz` specifically, sample-read a few headers to see
     what year the corrected timestamps fall in.
- If the corrected dates are within VW's active era (post-2012), the
  files are in scope and we should add a regex pattern + recorder-aware
  conversion path.
- If the corrected dates are still pre-2012 (or before the station's
  active era), exclude them.

**Don't auto-exclude based on filename date alone** — the WNRO mechanism
means filename dates are unreliable for this cohort by design.

Linked: [[reference-source-stats-tooling]],
[[project-rt130-per-channel-files]] (most likely cohort: RT130-via-Reftek
ingestion path).

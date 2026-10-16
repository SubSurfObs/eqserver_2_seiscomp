---
name: project-rt130-per-channel-files
description: "RT130 borehole stations (LOYU, WILU, TRPU, MOSU, SGWU) wrote SINGLE-CHANNEL miniSEED files via pre-ingest conversion. ~1.2M such files visible in EqServer manifest as 'unmatched'. Convert path must aggregate per-channel sources into 3-component output. Confirm in test env before launching scan 2."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Surfaced 2026-06-21 by `scan/investigate_unmatched.py`.

The five VW RT130 borehole stations — **LOYU, WILU, TRPU, MOSU, SGWU** —
wrote miniSEED in **single-channel-per-file** format. The filename grammar
encodes the channel in the basename. Two flavours seen so far:

- `YYYY-MM-DD HHMM SS STA_HH2.mseed.zip` (channel = HH2, e.g. LOYU 2014)
- `YYYY-MM-DD HHMM SS STA_001.mseed`   (sequence number suffix, e.g. WILU 2016)

Counts (from `_unmatched/_network_summary.txt`):
- ~856k files `STA_001.mseed` (mostly WILU)
- ~312k files `STA_001.mseed.zip`
- ~80k files `STA_HH2.mseed.zip` (LOYU)
- Plus per-channel variants at other stations (~1.2M total per-channel
  files network-wide)

**Operator context** (2026-06-21):

- RT130 native format is RefTek proprietary. Files in EqServer are the
  output of a pre-ingest conversion step (not done by current operator)
  that converts the proprietary RefTek stream to 1-minute miniSEED files.
- These stations are no longer in the active VW network — they were
  decommissioned years ago. But their archive contributions still matter
  for the long-term continuous record.
- The user EXPECTED the conversion pipeline already handled these. The
  fact they appear as "unmatched" in our grammar means scan-1's
  classification missed them entirely — which means our conversion may
  have silently dropped these stations' data.

**How to apply:**

- Add new regex patterns to `scan/level1.py` filename classifier for the
  RT130 per-channel variants. Tag them with `source_type='telemetry'`
  (they were telemetered, pre-converted from RefTek), `channel_suffix`
  set to the parsed channel (`HH2`, etc.), and a recorder hint of `rt130`.
- The cross_source / coverage_planner must aggregate per-channel files
  into 3-component output. Don't drop the channel suffix as a "stub"
  signal in the existing classifier — for RT130, the per-channel files
  ARE the only source.
- BEFORE launching scan 2: pick at least one day per affected station
  (LOYU 2014, WILU 2016, SGWU, TRPU, MOSU) into the test env, run
  conversion via test_env_run.py, verify via test_env_verify.py that
  all 3 channels emerge with full coverage.

Linked: [[reference-source-stats-tooling]] (the discovery tool),
[[project-eqserver-telemetry-only-via-network]] (the telemetry path
documentation).

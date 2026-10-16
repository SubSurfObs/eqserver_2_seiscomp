---
name: reference-source-stats-tooling
description: "New source-characterisation scripts under scan/ + outputs under metadata/source_stats/. Built 2026-06-21 to do the discovery work that should have preceded scan 1. Use these to answer 'what shape is the source data?' before any new scan."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Built 2026-06-21 during the planner-design effort for the second VW conversion
attempt. Together these answer the questions that, had they been asked, would
have caught the multi-tele-stream and single-channel-disk patterns before
scan 1 launched.

## Scripts (all under scan/)

- **categorize_source.py** STA — per-station day-shape histogram (A_pure /
  D_pure / E_multivar / AB_mixed / C_partial / weird / sparse / empty),
  coverage percentages, full per-day classification list, one representative
  day per bucket. Output: `metadata/source_stats/<STA>.{json,txt}`.

- **discovery_audit.py** STA — unknown-unknown surveillance. Lists every
  distinct combination of manifest column values per station, flags
  ambiguous signature pairs (the multi-tele-stream signal), filename-grammar
  coverage with samples of unmatched, bimodal-size flags per signature.
  Output: `metadata/source_stats/_discovery/<STA>.{json,txt}`.

- **channel_epoch_scan.py** STA — yearly-sampled header reads per
  source kind. Detects which channels the recorder was writing over time.
  Output: `metadata/source_stats/_epochs/<STA>.{json,txt}`.

- **investigate_unmatched.py** — tokenises unmatched filenames into shape
  templates, pools across all _discovery outputs, deep-dives top offenders.
  Output: `metadata/source_stats/_unmatched/{<STA>.json,_network_summary.{json,txt}}`.

- **test_env_build.py** add|rebuild|list|verify_mirror|rebuild_time_index —
  segregated test environment per (sta, date): byte-identical source
  mirror, regenerated manifest via current level1.py, regenerated plan via
  current plan_generator.py, time-index DB recording every source file's
  per-trace start/end/channel/npts. Lives on local disk at
  `/home/.../test_env_classb/` to avoid CIFS locking issues.

- **test_env_run.py** preflight|convert — invokes production phase3 against
  the test env. Pre-flight refuses if it sees unknown classifications.

- **test_env_verify.py** STA YYYY-MM-DD — opens SDS output, finds gaps,
  back-traces each gap to the time_index to classify as `pipeline_loss`
  (source had it, SDS dropped it) or `source_gap` (genuinely no source).
  This is the load-bearing tool for any "does this fix work?" question.

## Key findings (committed to repo 2026-06-21)

- 16 of 41 VW stations have the multi-tele-stream pattern (~24 M rows
  network-wide; not just STBK).
- 34 of 41 VW stations have unmatched-grammar filenames (~4 M files
  total; still being characterised by `investigate_unmatched.py`).
- 32 single-channel disk eras across 26 stations — "disk recorder
  configured to vertical only for years" is the NORM not the exception.
  Without the channel-epoch lookup, the planner silently drops CHN/CHE
  for huge date ranges.

## How to apply

Before any new scan / re-conversion of any subset of VW:
1. Run `categorize_source.py` on every station in scope. Note any newly
   appearing buckets.
2. Run `discovery_audit.py` on every station. Read every
   `ambiguous_signature_pairs` section. ANY non-empty pair is a "stop
   and investigate" signal.
3. Run `channel_epoch_scan.py` on every station. Confirm the planner
   has channel-availability lookup for every (sta, year) pair in scope.
4. If `investigate_unmatched.py` shows non-trivial unmatched counts for
   any station in scope, characterise those files before continuing.
5. THEN run conversion via `test_env_run.py` on at least one
   representative day per bucket per station. Use `test_env_verify.py`
   to confirm clean output before scaling.

Don't repeat the scan-1 mistake of trusting that the manifest's
filename-only classification is complete. The data itself tells us
what's there if we ask the right query.

Linked: [[feedback-verify-before-claiming]], [[feedback-use-station-dbs-first]],
[[project-eqserver-telemetry-only-via-network]],
[[project-class-a-vs-class-b-framing]].

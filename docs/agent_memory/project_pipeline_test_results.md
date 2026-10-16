---
name: project-pipeline-test-results
description: Multi-unit pipeline test run on 2026-06-01 against an isolated mediaflux proxy LT + local bare ledger. All 7 scenarios passed including the falsifiability control. Year-scoping fix verified end-to-end. Cleanup year-safety mitigated by LT-match-required design.
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**On 2026-06-01 a multi-scenario pipeline test ran against an isolated proxy LT + local-bare ledger, with no writes to real LT or real github. All scenarios passed.**

## Test environment

- Proxy LT: `/mnt/test_lt/eqserver_pipeline_test_20260601/` on dev1 (and ro on staging VM
  for cleanup access), CIFS-mounted from
  `//mediaflux.researchsoftware.unimelb.edu.au/proj-6700_sds_other_networks-1128.4.1538`.
- Test ledger: fresh clone at `/var/tmp/test_round_2026-06-01/test_ledger_clone`,
  origin = local bare `/var/tmp/test_round_2026-06-01/test_ledger_bare.git`.
  No external remote — confirmed by grep against the URL list.
- Test staging: `/mnt/seiscomp_staging/test_round_2026-06-01/seiscomp_archive`.
- Test stations: VW.HOLS years 2018, 2019, 2020 with short 3–5 day windows.

## Scenarios + outcomes

| # | Goal | Result |
|---|---|---|
| **S0** | Falsifiability: patch promote.py to strip `--year`, prove the year-leak appears (so S1's pass means something). | PASS. Unfixed apply for HOLS 2018 wrote 18 day-channels (BOTH years) under single run_id. 2019/VW/HOLS.events.jsonl tagged with HOLS 2018 run_id — exact yesterday-bug signature. |
| **S1** | Fixed promote (passes `--year`) against same setup. | PASS. Apply for 2018 wrote 9 day-channels (only 2018). Apply for 2019 wrote 15 (only 2019). Distinct run_ids per year. Race window did not stay open (hot-cache 12s phase3), but apply.py walked staging with BOTH years' files fully present at apply time — a strict superset of the dynamic race. |
| **S2** | SKIP path: re-convert HOLS 2018 (byte-identical to LT). | PASS. apply.py decided `skip=9`, LT bytes unchanged. This is the "VW direct-seedlink already in LT" case the operator was worried about — never overwritten. |
| **S2b** | OVERRIDE path: truncate one LT day-file, re-convert. | PASS. apply.py decided `override=1 skip=8`, promote.py routed to `held.jsonl` with action='held' reason='overrides > 0'. No `.commit.log` produced; LT bytes unchanged. |
| **S3** | Cleanup year-safety witness. Plant a file at staging 2020/VW/HOLS, run cleanup wrapper (cleanup.py has no `--year`). | PASS. Witness SURVIVED unchanged. Cleanup deleted 2018+2019 staged files (LT matches) but skipped 2020 (no LT counterpart). The year-blindness gap is real in shape but defanged by LT-match-required. `KNOWN_GAP=NO`. |
| **S4** | Provenance triangle walk + isolation summary. | PASS. All 24 events.jsonl lines have their `policies/<sha>.yaml` + `runs/<rid>/run.json`. Real ledger HEAD unchanged at 1e01e77. Real github main unchanged at 1e01e77. |
| **S4b** | Real autocommit (no `--no-autocommit`) to test bare. | PASS. Bare advanced 1e01e77 → a30eeeb with commit message `apply: VW.HOLS from eqserver_VW_HOLS_20260601T005508Z (write=10 override=0 skip=0 fail=0; source=eqserver)`. Real github main still 1e01e77. |

## What the test established

1. **Yesterday's race cannot recur** — apply.py `--year` filter has been verified to scope the staging walk correctly even when next-unit's phase3 has written staging files.
2. **Direct-seedlink LT data is safe** — when LT has equal-or-more samples than staging for a given day-channel, apply.py decides skip, never override.
3. **Genuine gap-fill cases are gated** — when staging has materially more samples than LT, override fires and promote.py refuses to `--commit`, surfacing the unit in `held.jsonl` for human review.
4. **Cleanup year-blindness is safe in practice** — cleanup.py walks station-wide across all years but the LT-size-match precondition prevents deletion of any file LT doesn't have.
5. **Provenance audit trail is complete** — every events.jsonl line has its policy + run records.
6. **Ledger autocommit isolation works** — autocommit pushes to whatever origin the clone has; pointing origin at a local bare keeps test commits off real github.

## Test artifacts (still on disk for forensic review)

- Logs: `/var/tmp/test_round_2026-06-01/{convert_logs,promote_logs,cleanup_logs}` on staging VM + dev1.
- Bare repo with new commit: `/var/tmp/test_round_2026-06-01/test_ledger_bare.git` on dev1.
- Clone working tree: `/var/tmp/test_round_2026-06-01/test_ledger_clone` on dev1.
- Test staging output: `/mnt/seiscomp_staging/test_round_2026-06-01/`.
- Snapshots / hashes: `/var/tmp/test_round_2026-06-01/S*_*.txt` (pre/post hashes, mtime baselines).

To preserve forensics, leave these in place until sign-off. Teardown removes the
mount (`sudo umount /mnt/test_lt` on dev1 + on staging VM); the staging mediaflux
volume keeps the project scratch dir `eqserver_pipeline_test_20260601/` until
operator removes it.

## Known follow-up items (not failures, just records for the next session)

- **Race window rigour**: S1 ran with HOLS 2019 phase3 finishing in 12 sec (hot
  cache), so the dynamic race didn't actually stay open. Apply walked with both
  years fully present in staging instead. The static case is a superset of the
  dynamic case for this fix, but if we want to prove the fix under a *guaranteed*
  open race window, drop caches with `echo 3 > /proc/sys/vm/drop_caches` or use a
  larger day window (~30 days) before launching the bg phase3.
- **Cleanup.py `--year`**: nice-to-have for explicit year scoping. Not safety-critical
  per S3. File as low-priority follow-up.
- **PiesMo defer_conversion rationale for DU**: still needs the review noted in
  [[project-du-sweep-preconditions]] before launching DU.

---
name: project-first-sweep-attempt
description: "First VW production sweep attempt on 2026-05-31. End-to-end pipeline worked on isolated single units, but failed on the multi-unit case due to an apply.py scoping race. Sweep halted. Three fixes applied; restart deferred until testing exercises multi-unit pipelining."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**State at end of 2026-05-31 session.**

LT contents from this session's writes (`/mnt/seiscomp_archive/`):
- 2022/VW/HOLS days 1-7 (21 day-channels) — verification of auto-mirror fix
- 2023/VW/HOLS days 1-7 (21 day-channels) — first end-to-end test
- 2025/VW/BEST 46 days (138 day-channels)
- **2024/VW/BEST days 001-004 (12 day-channels) — TAGGED WITH BEST 2025 RUN_ID by mistake; legitimate data, wrong provenance label**

Ledger commits: `7a1a0e7` (HOLS 2023), `8f8e3b2` (HOLS 2022), `ae8b616` (BEST 2025).

**Why:** apply.py was scoped `--net VW --sta BEST` only — no year. While promote.py was draining BEST 2025's `convert_done` entry, convert.py had already started BEST 2024 phase3 as the next subprocess; that phase3 had written `/staging/2024/VW/BEST/2024.001-004` to staging by the time apply.py walked. Apply.py promoted everything it found under the station, attaching the BEST 2025 run_id to all of it.

**Fixes applied 2026-05-31 night:**
1. `sds_staging_ledger/apply.py` @ `1e01e77` — added `--year` (repeatable); staging-walk filters by year before --day filter.
2. `eqserver_2_seiscomp/scan/run_production_promote.py` @ `026917a` — passes `--year str(entry["year"])` to every apply.py invocation.
3. `eqserver_2_seiscomp/scan/run_production_convert.py` @ `026917a` — refuses `status: BLOCKED` and `defer_conversion` outright; skips `status: needs_review` unless `--include-needs-review` is passed. 7 of 41 VW plans are BLOCKED (BEST, BRIG, HDDL, KRAN, LOCU, MARD, WPSH); 8 are `needs_review`; 26 `ok`.

**Not fixed, but known:** robust detach for long-lived launchers. nohup + `&` got SIGHUP-killed when the parent SSH disconnected, even though the python process should have survived. Next launch needs `setsid` or `systemd-run --user`. See [[feedback-test-the-pipeline-not-just-the-unit]].

**Open items for tomorrow:**
- Decide whether to relabel or leave the 12 stray BEST 2024 LT files (currently tagged with the BEST 2025 run_id).
- Build the testing layer that would catch this kind of multi-unit race BEFORE re-launching the sweep.
- Then re-attempt the sweep.

**Things validated by this session (don't re-test):**
- Single-unit end-to-end works: convert → manifest → promote dry-run → override gate → commit → ledger autocommit → cleanup.
- The auto-mirror plan fix (HOLS 2022 zero-intervention proof at `d68138d`).
- The override gate semantics (`if lt_samples >= st_samples: skip` — verified by reading `apply.py:decide`).

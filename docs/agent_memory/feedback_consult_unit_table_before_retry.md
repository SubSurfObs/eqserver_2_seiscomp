---
name: feedback-consult-unit-table-before-retry
description: "Before any retry-class decision in this project, run `sweep_status.py --unit-table` for the affected units. The state column is the gate; convert_failed.jsonl alone is misleading."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

For any decision class involving retrying, redoing, or "is this unit still outstanding?":

**Run `python3 scan/sweep_status.py --queue-dir /mnt/seiscomp_staging/eqserver_sweep --unit-table --unit NET.STA.YEAR,...` FIRST.** Read the `state` column. That is the answer. `convert_failed.jsonl` read in isolation is not.

State semantics (precedence: cleaned > promoted > held > failed-unresolved > failed-resolved > converted-not-yet-promoted > not-attempted):
- `cleaned` / `promoted` / `failed-resolved` → **do not retry, already handled**
- `held` → triage in held.jsonl; do not auto-retry
- `failed-unresolved` → this is what "needs retry" actually means
- `converted-not-yet-promoted` → currently in pipeline, do not retry
- `not-attempted` → never tried, ok to queue

**Why:** 2026-06-13 incident — I retried 6 VW units (DDNE/DDSW/LOYU/LRNW×2/LRWS) by reading `convert_failed.jsonl` and treating its append-only entries as the current state. They had actually been successfully recovered last week via `run_recovery_register.py` — `promote_done.jsonl` had the recoveries; I just hadn't looked. 7 hours of compute and a sharp operator correction. Operator: "If you told me you needed to rerun something and then discovered that you didn't, there's an inconsistency."

The Option B refactor (committed 1d56c23 on 2026-06-14) added:
- `action="resolved"` events in `convert_failed.jsonl` written by `run_recovery_register.py` on success
- `sweep_status.py --unit-table` mode that folds them
- `backfill_resolved_events.py` for historical entries
- CLAUDE.md "Sweep recovery registers" now points at `--unit-table` as the machine-checkable layer

**How to apply:**
- Any retry/redo prompt → `--unit-table` first.
- If state is `cleaned`, `promoted`, `held`, or `failed-resolved`, stop and report to operator: "this unit is already in state X."
- Only act when state is `failed-unresolved` or `not-attempted`.
- If `--unit-table` and the narrative `docs/scan*_recovery_register.md` disagree about a unit, that's a bug — investigate before retrying.

Linked: [[feedback-check-audit-trail-before-speculating]] (the same incident's root cause).

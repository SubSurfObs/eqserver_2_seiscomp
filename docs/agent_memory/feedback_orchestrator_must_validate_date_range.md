---
name: feedback-orchestrator-must-validate-date-range
description: "run_production_convert.py must validate that phase3's start/end_date is a full year (or that partial_completion_reason explains the truncation) before appending to convert_done. Without this guard, leftover test-run invocations with 7-day windows get marked complete and never re-attempted."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When phase3 reports back to `run_production_convert.py` after a unit run, the orchestrator must verify:

```
manifest.phase3_invocation.argv.end_date - argv.start_date >= 0.9 * full_year_days
   OR
manifest.eqserver.partial_completion_reason  is set
```

If neither holds, the unit is NOT complete and convert_done must NOT be appended. Instead, log a warning + skip the convert_done append so the orchestrator (or a human) retries the full year on next pass.

**Why:** 2026-06-18 incident — VW.HOLS 2022 + 2023 were each converted with `--start-date YYYY-01-01 --end-date YYYY-01-07` from leftover test invocations on 2026-05-31. Phase3 returned `items_succeeded=7` for each. Orchestrator marked them as fully promoted. **358 days × 2 years = 716 station-days of HOLS data went silently uncaptured for 18 days** until the source-vs-LT gap audit surfaced it. Fortunately scope was small (3 of 339 VW manifests had clipped ranges, the other 336 were full-year). But a one-line guard would have caught it at convert-time.

**How to apply:**
- Any future change to `run_production_convert.py` that touches the convert_done append path should preserve or strengthen this guard.
- Any orchestrator review during pre-DU work should verify the guard is present and tested.
- If considering a sub-year date range as a legitimate operation (e.g. recovery), it must set `partial_completion_reason` so the audit can distinguish intentional from test-leakage.

Linked: [[feedback-consult-unit-table-before-retry]], [[feedback-check-audit-trail-before-speculating]].

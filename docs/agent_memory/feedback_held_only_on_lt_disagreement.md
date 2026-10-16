---
name: feedback-held-only-on-lt-disagreement
description: "Held queue fires only when LT has data that disagrees with staging (override). When LT is absent, apply.py action=write and promote.py auto-commits. Don't conflate 'broken LT bytes' with 'missing LT bytes'."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

apply.py decide() returns one of three actions per (day, channel):

| Condition | Action | Held queue? |
|---|---|---|
| `lt_samples == 0` (file absent) | **write** | No — auto-commit |
| `lt_samples < staged - tolerance` | **override** | YES — overrides > 0 → held |
| `lt_samples >= staged` | **skip** | No — nothing to do |

Held queue is the gate for **LT vs staging disagreements**, not LT absence. If a previous sweep clipped before writing some days, those days have `lt_samples_before = 0` and apply.py treats them as fresh writes — no human review needed because there's nothing pre-existing to compare against.

**Why this matters:**

Recovery work (test-run-clip recovery, missing-day recovery, sweep restart fillers) almost always falls in the "LT absent" case, not the "LT disagreement" case. So it auto-promotes through the production watcher without triggering held.

Cases that DO trigger held:
- Re-conversion produced more samples than existing LT (e.g. side-load Class B fix where stale-engine LT bytes have fewer samples than current-engine staging output)
- LT bytes were partial-day (e.g. an earlier convert wrote a 12-hour day file before a crash, full-day re-conversion now has 2× the samples)
- An override flag was explicitly forced via `--mode overwrite`

**How to apply:**

When planning recovery work that might end up in LT:
1. Check whether the LT path EXISTS first. If absent (test-run-clip class), expect write + auto-commit through promote.py. No human review needed.
2. If LT exists with smaller sample counts (Class B class), expect override + held.jsonl. Per-entry triage required per [[feedback-held-queue-is-load-bearing]].
3. If LT exists with matching or larger sample counts (already-promoted class), expect skip. No work needed.

Don't conflate the three — they have different operational implications.

**Real example 2026-06-18 → 06-19:** HOLS 2022 had been clipped after 8 days by the test-run-clip bug. The other 357 days were never written to LT. After re-conversion ran phase3 on 06-18, apply.py decided:
- 8 days: skip (LT == staged from original sweep)
- 357 days: write (LT absent, action=write)
- 0 days: override

Auto-promoted in ~7 hours wall-clock without operator intervention.

Linked: [[feedback-held-queue-is-load-bearing]], [[feedback-check-audit-trail-before-speculating]], [[project-disk-to-sds-decide-rule]].

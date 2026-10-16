---
name: feedback-test-the-pipeline-not-just-the-unit
description: "Single-unit end-to-end tests prove the per-(sta, year) chain works but do NOT prove the orchestrator-as-a-pipeline works. Before re-launching a multi-unit sweep, test the multi-unit case — overlapping units, races, status-gate misses, partial state in staging. Both verification tests this session were single-unit."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**The rule:** before re-launching the production sweep (or any multi-unit pipeline), explicitly test the **pipeline** — concurrent units, races, partial state — not just the **unit**.

**Why:** on 2026-05-31, two single-unit tests (HOLS 2023 days 1-7, then HOLS 2022 days 1-7) both passed end-to-end with zero intervention. That gave false confidence that the production sweep was ready. The very first multi-unit run (BEST 2025 → BEST 2024 back-to-back) hit a race: apply.py was scoped `--net VW --sta BEST` (no year), and while promote.py was draining BEST 2025's queue entry, convert.py had already started BEST 2024 phase3 — by apply.py walk time, BEST 2024 days 001-004 existed in staging and got promoted under the BEST 2025 run_id. Three independent issues (apply.py year scoping, convert.py status:BLOCKED gate, nohup not actually detaching) had all evaded the single-unit test by construction.

**How to apply:**

1. **For any new orchestrator change, write a test that exercises ≥3 consecutive units.** A 1-unit test cannot catch:
   - Apply.py picking up files written by the NEXT unit's in-flight phase3 (the actual bug today).
   - Promote.py's queue-resume logic skipping or duplicating units across runs.
   - Cleanup racing against the next unit's writes.
   - convert.py's iteration order doing something unexpected at unit boundaries (status gate miss, year arithmetic).
2. **Test with units that share a station, not just different stations.** The race fires specifically when consecutive units write to the same `/staging/<YEAR>/<NET>/<STA>/` parent. Two different stations would not have triggered today's bug.
3. **Exercise resume.** Kill convert.py mid-unit; restart; verify it picks up correctly without re-doing or skipping. Today's nohup-failure showed mid-unit kills happen in real life.
4. **Test the status gate explicitly.** Have a BLOCKED plan and a needs_review plan in the test fixture; assert convert.py refuses them.
5. **Inspect staging mid-run.** A single-unit test that runs convert→promote→cleanup serially never has anything in staging at decision time. Multi-unit tests should pause between phases and assert "what's in staging RIGHT NOW does/doesn't match my expectations."

**Concrete failure to learn from (2026-05-31):**

The full-sweep launch (330 units) was the FIRST time the pipeline ever ran more than one unit consecutively. The single-unit tests proved the per-unit mechanics, the auto-mirror fix, the override gate, the autocommit — none of those failed today. What failed was inter-unit interaction, which the test plan never exercised. The user's words: "This is exactly the sort of edge cases I would have hoped were picked up by testing."

The cost was a small one (12 stray staged-but-promoted files with a wrong run_id label — legitimate data, traceable, no LT corruption), but the next race could have been silent corruption.

---
name: feedback-cold-cache-discipline
description: "For ALL perf tests on this project's pipeline, explicitly control for cold vs hot NFS cache state — the gap is large enough to invalidate apples-to-oranges comparisons silently"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When designing any performance test on this project's pipeline (phase3, stress
harness, workers sweep, multi-VM tests, single-day profile, anything producing
a "Round N" measurement), **explicitly control for cold vs hot NFS cache state**.
The cold/hot wallclock gap is large enough to make apples-to-oranges comparisons
look like real wins or regressions when they're cache artefacts.

**Why:** 2026-05-31 concrete failure. I designed a 31-day STBK rerun at
pool=2×ws=4 to A/B against the May 28 preliminary's 10.5 s/day STBK number.
The result (13.4 s/day) looked like a regression of the zip-fix optimization,
but the user pointed out the 31-day window was dominated by cold cache while
the preliminary's 2150-day run was overwhelmingly steady-state hot cache. The
"regression" was a test-design artefact. Critically, **I had already documented
the cold-cache problem in PERFORMANCE.md Exp 3 caveats** — the knowledge was
in my context; I just didn't apply it when designing the test. The user
flagged it after I'd already presented the wrong conclusion.

**How to apply:**
1. **Before launching ANY perf measurement**, state explicitly in the rationale
   which regime you're targeting (cold-cache, hot-cache, or both) and why.
2. **Date-window length matters.** Tens of days at workers=4 is NOT enough to
   reach hot-cache steady state. Hundreds of days are. If steady-state hot-cache
   is the goal, run long enough — minutes-scale wallclock is suspect.
3. **Alternative for clean cold-cache comparisons:** deliberately drop page
   cache between configs (`sudo sh -c 'sync && echo 3 > /proc/sys/vm/drop_caches'`).
4. **Always label results explicitly as cold-cache or hot-cache** in
   PERFORMANCE.md / commit messages / chat replies. Never let a number sit
   without its cache-state annotation.
5. **Never compare across regimes silently.** If preliminary is hot-cache and
   the new test is cold-cache (or vice versa), call out the mismatch and
   either redesign the test or qualify the conclusion heavily.

This rule applies to all perf-related Experiments in `PERFORMANCE.md` and
any new ad-hoc speed tests. The PERFORMANCE.md schema should grow to require
a "Cache regime" field per experiment alongside Objective/Configuration/Outcome.

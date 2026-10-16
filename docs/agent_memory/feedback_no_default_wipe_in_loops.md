---
name: feedback-no-default-wipe-in-loops
description: "Never put rmtree-style 'clean slate' wipes as default behaviour in code that may be called in a loop. Wiping must be opt-in via explicit flag. Bulk wrappers around per-item functions must audit those functions for state-modifying side effects before iterating."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Incident 2026-06-22. The `test_env_run.py wipe_staging_day` function did
`shutil.rmtree(<staging_sds>/<year>/VW/<sta>/)` as its default and was called
unconditionally inside `cmd_convert`. The original purpose was "clean slate
per single-day test invocation", which is reasonable on its own.

When I added `convert_all` today and looped `cmd_convert` over 220 days,
each iteration wiped the previous iteration's output if the days shared
a `(sta, year)`. By end of the 3-hour sweep, only the LAST day per
station-year survived. compare_lt then reported NO_STAGING for 130 of
224 days, which I initially misread as widespread conversion failures.

**Why:** The user worked round-1 of the production VW sweep with code
that never wiped staging. The wipe in the test harness was MY ADDITION
in an earlier session for repeatable single-day tests. Adding a bulk
iterator on top of it inverted its safety.

**How to apply:**

1. Never `rmtree` as default behaviour in code that may be called in a
   loop by a future wrapper. Wiping must be opt-in (`--clean-slate`,
   `clean=True`, etc.).
2. Any `rmtree` / `os.unlink` / `shutil.rm*` MUST have an absolute path
   guard — e.g. assert the target starts with `test_env_classb/` before
   firing.
3. Before adding a bulk-iteration mode (`*_all`, `bulk_*`) over an
   existing per-item function, audit the per-item function for
   state-modifying side effects to siblings of the current item.
4. The production VW sweep code (`run_production_*.py`,
   `phase3_driver.py`) never wipes staging — it merges with existing
   bytes. That's the reference pattern. Test harnesses should not
   diverge from it without strong justification.
5. When asked "what caused X" about something I wrote (even in an
   earlier session), check git blame BEFORE attributing it to "earlier
   code" — that framing was unfair distancing the first time.

Linked: [[feedback-verify-before-claiming]] (sibling rule).

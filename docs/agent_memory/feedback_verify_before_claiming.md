---
name: feedback-verify-before-claiming
description: "Every concrete assertion about 'X exists / X is true / X works this way' must be backed by a tool output in the same response that directly shows X. No inference from partial ls / partial code reads / half-remembered docs."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When analyzing this project's code or data, every concrete claim of the form "X is true" / "X exists" / "X doesn't exist" / "the code does X" must be backed by a tool call output in the same response that directly demonstrates the claim. No inference from partial evidence, no extrapolation from a snippet.

**Concrete failure modes from 2026-06-20 conversation (one session):**
- Claimed EchoPro is "structurally immune" to a write_sds bug. Wrong — verified by the LT trace-count scan flagging pure-EchoPro stations.
- Claimed EchoPro has "fewer source files" than Gecko. Wrong — both file every minute.
- Treated the disk/tele duplication as a discovery. Wrong — it's foundational and documented in CLAUDE.md.
- Claimed phase3 doesn't apply the documented dedup priority for Gecko. Wrong — `query_cross_source_day_files` (line 252 of phase3_driver.py) does, via `cross_source.select_files_for_day`.
- Claimed STBK 2022-10-23 has "no disk files, only telemetry." Wrong — 1440 disk files exist; the wrong claim came from looking at only the first 10 `ls` entries which happened to be the space-prefixed telemetry files.

Each of these wasted a conversation turn (sometimes more) on correction, and forced the user to hold both the actual narrative AND the running list of my misstatements in their head simultaneously. Operator stance 2026-06-20: "it causes a cognitive burden on me because I have to fix them while keeping track of the actual narrative of the problem."

**How to apply:**

Before stating "the project does X" / "the source has Y" / "code path Z applies/doesn't apply some rule":
1. Stop. Do not type the assertion.
2. Run the tool call that would directly show whether the assertion is true (full grep, full ls, full code read, full sample of the data).
3. Only after the output is in the response, state what the output shows.

If a question requires more than one tool call to verify (e.g. "does phase3 apply dedup?" requires reading the worker dispatch AND the query function AND the selector function), make ALL those calls before stating the answer. Partial verification is worse than asking the user to wait.

For exploratory questions ("I think X but I'm not sure"), say "I haven't checked yet" and then check — don't precommit to a hypothesis that costs both of us time to disprove.

**The user-facing notebook is the right enforcement mechanism for diagnosis-heavy work** — each cell IS a verified claim; the notebook's outputs are the assertions. Prefer notebook-driven verification over prose claims for any non-trivial diagnostic.

Linked: [[feedback-no-station-heuristics-without-registry]] (same family — register-then-claim discipline).

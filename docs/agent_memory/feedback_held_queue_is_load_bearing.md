---
name: feedback-held-queue-is-load-bearing
description: "Don't auto-commit held entries via apply.py --commit, even when \"tuning tolerance\" or \"matching disk_to_sds.\" The held queue is a human review gate; disk_to_sds field history shows the rule was wrong the one time it fired."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When held.jsonl entries accumulate (apply.py decide() found
override > 0), the right move is **NOT** to bulk-run
`apply.py --mode decide --commit` to drain them. The held queue is
a load-bearing human-review gate, not paperwork.

**Why:** Per disk_to_sds 2026-06-10 field-history note: the
override branch has fired in production exactly ONCE (WLSH swap
day). The automatic decide() rule was WRONG that one time —
disjoint day-halves (LT had morning telemetry, conversion had
afternoon SD card). Auto-committing would have destroyed real
data. Only the human gate caught it.

So:

- **Held entries stay held** until a human inspects them per-entry.
- The decide() rule keying on sample_count + tolerance is fine in
  general, but it CAN'T detect the disjoint-day-halves case where
  both sources are individually incomplete but cover different
  time-windows. That case looks like "the bigger one is more
  complete" to decide(), and the override is wrong.
- The doubled-LT-day trap is the OTHER known decide() failure
  mode (telemetry artifact, ratio ~2.0). Needs duration-ratio QC
  to catch.
- "Just raise --tolerance to 0.05" sounds tempting but doesn't fix
  either of these. Tolerance is the wrong knob for "which source is
  more correct on this day."

**How to apply:** When someone proposes draining the held queue
in bulk — including the eqserver agent's own past framing of "80%
size rule" or "match disk_to_sds with apply.py --commit on each
held entry" — push back. The right shape is per-entry triage:

1. Run `apply.py --mode decide` (dry-run) for each held entry.
2. For each (date, channel) flagged override, manually look at
   LT vs staging for that specific day.
3. Compare duration-ratios, time-coverage windows, sample counts.
4. Decide per-entry whether the override is safe.
5. Commit individually after that human judgement, not in bulk.

That's operator work, not autonomous-agent work. Surface the held
queue with diagnostic context if asked, but don't drain.

Also: the eqserver agent (me, 2026-06-10) was wrong on every axis
about an "80% size rule" — see [[project-disk-to-sds-decide-rule]]
for the actual rule (sample-count + tolerance-of-a-full-day, not
size-based, already wired source-kind-agnostic). Don't drift back
to the size framing.

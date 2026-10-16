---
name: feedback-check-audit-trail-before-speculating
description: "For \"who wrote these LT files?\" — read the mtime first. If recent, it was this project; legacy bash+Java pipeline is not a candidate."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When LT shows files I don't immediately recognise the source of:

**Look at mtime first. That's the whole answer in almost every case.**

- mtime within the last few weeks → this project's pipeline wrote it. Done. Don't speculate further.
- The legacy bash+Java EqConvert pipeline is **NOT a candidate** for any in-scope station-network combo. It has been dormant for years. The operator was direct: "at no stage would the legacy code have dealt with these stations." Do not suggest the legacy pipeline as an explanation — for VW, VX, DU, or any sweep target.
- Don't reach for events.jsonl / promote_done.jsonl / convert logs as the FIRST diagnostic. They're useful for "which specific run wrote it" but they're not where to START.

**Only after mtime says something surprising** (e.g., file from years ago, or future mtime, or quality flag mismatch) — *then* dig into the audit trail.

**Why:** 2026-06-13 incident — operator asked "where did these LT files come from?". I had already pulled mtimes showing 2026-06-07 and 2026-06-10 (3-6 days old). The answer was right there: this project's recovery runs from last week. Instead I speculated about the legacy pipeline (twice), then live seedlink, then dove into the events.jsonl audit trail. The operator: "Why don't you simply timestamp when they went in there? I mean, you've already given the game away there only three to six days ago."

**How to apply:**
- Question framing "where did these LT bytes come from?" → first answer is mtime. Period.
- If user-facing, lead with: "The LT files were written on YYYY-MM-DD" — let the user decide if that warrants a deeper look.
- Never suggest the legacy bash+Java pipeline as an explanation for in-scope station-network LT data. It's not a live system.

Linked: [[feedback-headline-first-status-updates]].

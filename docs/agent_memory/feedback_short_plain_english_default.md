---
name: feedback-short-plain-english-default
description: "Default reply style is short and plain English. Aim for ~4 sentences. No tables, lists, headers, or multi-section structure unless the operator explicitly asks for detail. Long, structured replies slow iteration. The operator will ask for more if they need it."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

The operator's request 2026-06-24:

> Could you please implement a policy of simple, plain English instructions?
> When you print out 400 words over multiple paragraphs, it's very hard.
> Could we bake this in? What you just gave me is perfect. If you did that
> every time, we would iterate far faster.

**Why.** Long structured replies (tables, headers, multi-paragraph
"zoom out / zoom in") force the operator to read carefully to extract
the answer. A 4-sentence plain-English summary is faster to absorb and
keeps iteration tight.

**How to apply.**
- Default reply length: ~4 sentences. Hard cap of one short paragraph
  unless the operator explicitly asks for detail.
- No tables, headers, bullet lists, or section structure by default.
- Plain English, not project jargon. "BEST's v1 LT lost about a minute
  of data at the start of each day" beats "Issue 1 midnight-boundary
  loss in pre-C units".
- Status updates: lead with the conclusion, then 1-2 sentences of
  supporting fact, then stop.
- The exemplar the operator approved (2026-06-24):
  > BEST's v1 LT lost about a minute of data at the start of each
  > day (a known bug from early in the v1 sweep). v2 fixes it —
  > about 12% of BEST day-files now have that minute of recovered
  > data. The other "different bytes" we saw before are just overlap-
  > handling style, not real data changes. The big side-load
  > contamination scare doesn't show up in the data at all — v2 and
  > v1 have identical fragmentation.
- When the operator asks for "the data" or "details" or "show me X",
  break out the structured stuff. Until then, don't.
- When proposing a sequence of next steps, plain prose: "First X.
  Then Y. Then Z." Not a numbered list with sub-bullets.

**Tension with [[feedback-headline-first-status-updates]].** That note
asks for a bolded plain-English one-liner before the detail. This note
takes it further: the one-liner is OFTEN the whole reply. Detail is
on demand, not by default.

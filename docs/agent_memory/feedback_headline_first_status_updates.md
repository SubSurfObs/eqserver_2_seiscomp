---
name: feedback-headline-first-status-updates
description: "For any status update or progress report, lead with a one-line plain-English headline a human can understand cold; only then unpick detail"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When reporting on the state of any in-flight or just-completed work, lead with **one bolded plain-English sentence** that someone walking in cold could understand — describing the *intent* of what was happening ("we set up processors to convert all the remaining VX stations") and *whether it's done*. Then optionally unpack detail below.

Avoid: pipeline-jargon headlines that only make sense if you've been in the conversation ("both tracks complete and the Echo fix worked end-to-end"). Avoid: leading with metrics tables that the reader has to decode.

**Why:** This user is running many Claude sessions in parallel and can't carry the per-session pipeline context in their head. A pipeline-jargon headline forces them to recall what "Track A" or "the Echo fix" means before they can extract the actual state. A plain-English headline lets them triage the update in seconds: "OK done, move on" vs "needs me to do something."

**How to apply:**
- Every status update, progress check, "update please", "what's happening" response, end-of-turn summary on a multi-step task: open with a single bolded headline using project-domain words (stations, conversions, networks) not pipeline-internal labels (tracks, fixes, SHAs, run_ids).
- Headline should answer "what was the goal" + "is it done." Nothing else.
- Below the headline, detail is fine — including tables, run_ids, file paths — for when they want to dig in.
- Doesn't apply to: ordinary work conversations, one-shot answers, code edits, debugging exchanges. Just status/progress.

Linked: [[user-profile]] — user juggles multiple Claude sessions across sibling repos.

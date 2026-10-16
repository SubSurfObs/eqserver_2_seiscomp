---
name: feedback-no-station-heuristics-without-registry
description: "Don't ad-lib station identity / network assignment from naming patterns alone. Check the registry first; if it's quiet, say \"I don't know\" rather than guess."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When asked about a station's identity, network, recorder, or deployment,
do NOT infer from naming pattern alone. Cross-reference the registry
(`metadata/station_registry.yaml`), the FDSN inventory under
`metadata/uploaded/`, or `uom_seismic_metadata/reference/history/`
before claiming a fact.

If those sources don't say, the right answer is "I don't know — looks
like X but unconfirmed" — not a confident heuristic guess phrased as
fact.

**Why:** 2026-06-09 — when asked which stations were left in the sweep
queue, I tagged `WPNH/WPSH` as "likely VW aftershock cluster (Woods
Point aftershocks per the naming heuristic)". Operator corrected: they
are Wilson's Promontory permanent VW stations, hundreds of km from
Woods Point. I had no basis for the claim and the registry already had
the correct answer if I'd looked. Operator named it "ad-libbing."
The CLAUDE.md aftershock heuristic is specifically `WPM<digit>Y`, not
all `WP*` — extending it was my error. See [[project-wp-naming-disambiguation]].

**How to apply:** Before stating a station's network/recorder/role:

1. Check the registry for an entry. If present, quote it verbatim.
2. If absent, check the FDSN inventory or wiki reconciliation.
3. If still nothing, flag explicitly: "Not in registry; happy to dig
   further but I don't have a fact for this yet."

Cheaper than ad-libbing and being wrong. The operator can spot a bad
guess quickly; a flagged "don't know" is much less expensive than
shipping a wrong inference into a report or doc.

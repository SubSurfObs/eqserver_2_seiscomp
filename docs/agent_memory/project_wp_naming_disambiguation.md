---
name: project-wp-naming-disambiguation
description: "WP-prefix stations split into two distinct deployments — Wilson's Prom (permanent VW) vs Woods Point aftershocks (temporary VX). Don't conflate."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

WP-prefix stations in this network are ambiguous and must be disambiguated
by the **3rd-and-4th character**, NOT by a blanket "WP* = aftershock"
heuristic.

| Pattern | Deployment | Network |
|---|---|---|
| `WPM<digit>Y` (e.g. WPM1Y, WPM2Y...WPM6Y) | Woods Point aftershock array | **VX** (temporary) |
| `WPNH`, `WPSH` | **Wilson's Prom** permanent stations | **VW** (permanent) |

**Why:** Why operator-confirmed 2026-06-09: I'd ad-libbed by extending
CLAUDE.md's `WPM*` aftershock heuristic to all `WP*` codes, which is
wrong. The locations are unrelated — Wilson's Promontory (south Vic
coast) and Woods Point (Vic alps) are hundreds of km apart.

**How to apply:** Whenever I encounter a `WP*` station code I haven't
seen before, look at the 3rd-and-4th characters and check the registry
explicitly. Don't infer network from prefix alone. Same lesson
generalises to `ABM*` (Apollo Bay numbered aftershock array — VX) vs
any future `AB-` codes that might mean something else; the pattern is
"check by character grouping AND registry, not by stem alone." Linked
to [[feedback-no-station-heuristics-without-registry]].

---
name: project-dd-station-naming
description: "DD-prefix VW stations: a 'B' in the 3rd or 4th letter means Minimus + Guralp Radian (~20m slimline borehole). No B = not Minimus, never was. Stations do NOT migrate between this and other recorder classes."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

DD-prefix VW stations follow a strict naming rule for borehole status
(operator-confirmed 2026-06-04):

- **B in 3rd or 4th letter** (e.g. DD**B**E, DDW**B**) → **Minimus
  recorder paired with Guralp Radian sensor**, deployed in a slimline
  borehole ~20 m deep. The Minimus + Radian combination is the entire
  identity of these sites.
- **No B in those positions** (e.g. DDNE, DDSW) → NOT Minimus. NEVER
  was. These are surface deployments using Gecko or similar
  conventional recorders.

**Rule**: stations do NOT change between the Minimus/Radian class and
the conventional surface-recorder class over their lifetime. No site
has ever migrated between these two physical-deployment classes.

**Why:** Trying to identify recorder type from per-day file-count
variation across years tempted a wrong inference 2026-06-04 (I
speculated DDNE might have transitioned from Minimus to Gecko based on
file-count signatures). The naming convention rules that out by
construction. If file-count variation appears within a DDNE-class
station, it's an operational pattern (telemetry duplication, repeater
behaviour, session-structure quirk) — not a recorder transition.

**How to apply:**

- When tagging recorder for DD-prefix VW stations, derive directly from
  the station code, not from header probing. DDBE and DDWB are Minimus;
  DDNE / DDSW etc. are Gecko (default for VW surface).
- The Minimus cohort in VW is exactly {DDBE, DDWB, SCM2} per
  [[project-overview]] and CLAUDE.md "Guralp Radian + Minimus" section.
  SCM2 doesn't carry the DD prefix; it's the exception to the naming
  convention.
- When the per-day file count for a non-B DD station varies year-over-
  year, look for operational causes (cross-source duplication, single-
  channel stub emission patterns), NOT recorder-type changes.

Related: CLAUDE.md "Guralp Radian + Minimus" section already documents
the {DDBE, DDWB, SCM2} cohort; this memory adds the explicit naming
rule that derives membership from the station code itself.

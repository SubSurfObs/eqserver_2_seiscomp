---
name: feedback-iterative-station-by-station-workflow
description: "For v2 (and any future) production resweep, the operator pattern is iterative station-by-station: one station, spot-test staging vs LT, apply, more testing, then add the next station via --stations BEST,OTHER. Days between stations are fine. The shared queue-dir (eqserver_sweep_v2/) marks completed units so re-runs only do new work. Don't launch the full cohort/network at once."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

When the operator asks for a "v2 single-station run" or any production
resweep on VW (or any network), the workflow is iterative — NOT a single
batch launch.

**Why:** Stated explicitly by operator 2026-06-23 when authorising the
first v2 BEST run. The 9-step loop is:

> 1. We start with one station.
> 2. We run it.
> 3. We do some spot tests of the staging versus the long term.
> 4. We run apply.
> 5. We do a bunch of testing.
> 6. We add another station.
> 7. We do some more testing.
> 8. We do this over several days.
> 9. We build out the remaining stations.

**How to apply:**
- For each new station, run all four pipeline stages
  (MANIFEST → PROFILE → PLAN → CONVERT) before considering the next one.
- Always preserve queue continuity by using the SAME `--queue-dir`
  across launches (`/mnt/seiscomp_staging/eqserver_sweep_v2/` for v2),
  so re-launches with `--stations BEST,OTHER` skip BEST and run OTHER.
- Encourage spot-tests and PROMOTE dry-runs between station additions.
  Days of pause between stations are normal, not a problem.
- Do NOT propose launching the full cohort or network at once unless
  the operator has explicitly opted into batch mode. The default
  expectation is the slow iterative loop.
- This is the same shape as the v1 sweep workflow, intentionally — the
  queue-dir mechanism in `run_production_convert.py` already encodes
  this pattern; we just need to keep using it.

Linked: [[project-pipeline-stages-vocabulary]] (the 5-stage names this
loop touches), [[project-next-session-startup-2026-06-23]] (this loop
was kicked off there).

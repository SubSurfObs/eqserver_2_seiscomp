---
name: project-pipeline-stages-vocabulary
description: "Locked 2026-06-23: pipeline is 5 executable stages + 1 manual foundation. Stage 0 REGISTRY (manual, station_registry.yaml + FDSN snapshots), Stage 1 MANIFEST, Stage 2 PROFILE, Stage 3 PLAN, Stage 4 CONVERT, Stage 5 PROMOTE. Authoritative reference is CLAUDE.md 'Pipeline stages and their dependencies' section."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Locked 2026-06-23 after iteration through diagnose/inspect/inventory
alternatives. The vocabulary replaces historical accidents `level1` and
`phase3`. Authoritative reference is the CLAUDE.md "Pipeline stages and
their dependencies" section at the top of the project.

## The locked names

| # | Stage | Type | Host | What |
|---|---|---|---|---|
| 0 | REGISTRY | manual / curated | Mac | station_registry.yaml + FDSN snapshots |
| 1 | MANIFEST | pipeline | Staging VM | walk NFS, build per-station DB |
| 2 | PROFILE | pipeline | Staging VM | per-station analytics from manifest |
| 3 | PLAN | pipeline | Staging VM | per-station conversion contract YAML |
| 4 | CONVERT | pipeline | Staging VM | source bytes → staging SDS |
| 5 | PROMOTE | pipeline (WRITE-HOST INVARIANT) | dev1 only | staging → LT atomic copy + ledger event |

## Why these specific names

- **REGISTRY** — already established project vocabulary (station_registry.yaml);
  Stage 0 captures the operator-curated foundation that the pipeline consumes
- **MANIFEST** — already in codebase (`manifest_recorder`, `--run-manifest`);
  rejects "INVENTORY" which clashes with obspy.Inventory station metadata
- **PROFILE** — clean replacement for "diagnose"; we're profiling each
  station's source data, not fault-finding. Rejects "INSPECT" which would
  shadow Python stdlib `inspect`.
- **PLAN** — direct, descriptive, matches the artifact (plan YAML)
- **CONVERT** — what the operator wants to do; replaces "phase3"
- **PROMOTE** — already established (`promote_done.jsonl`,
  `run_production_promote.py`); the data is promoted from staging
  (provisional) to LT (canonical)

## When to apply

- Always when discussing the pipeline architecture in new docs,
  comments, commit messages, or memory notes.
- Old historical names (level1, phase3, diagnose) still appear in
  current filenames; that's transitional. Don't rename files mid-sweep —
  schedule a separate clean rename pass when there's quiet time.
- Any new workflow component must slot into one of the 5 stages or
  articulate why it can't. "Stage 6" should be very rare.

## Where the dependency matrix lives

CLAUDE.md "Pipeline stages and their dependencies" has the
per-stage matrix of:
- Which project repos must be pinned (with SHA discipline)
- Which Python packages are required
- Which host each stage runs on
- The pre-flight commit-hash verification policy

That section is the single source of truth for per-stage dependencies
and provenance discipline. Updates to dependencies should land there
first, with this memory note serving as a pointer.

Linked: [[project-engine-provenance-incident-2026-06-01]] (the side-load
incident that drove the SHA-pinning discipline),
[[feedback-git-synced-across-hosts]] (the rule we tightened in response).

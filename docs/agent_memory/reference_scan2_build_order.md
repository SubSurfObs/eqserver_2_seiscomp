---
name: reference-scan2-build-order
description: "Canonical 6-step build order for the second-pass conversion pipeline. Anchor for any work on scan 2 after compaction. Do not press ahead with the test suite until Step 5 is real — the pieces don't talk to each other yet."
metadata:
  node_type: memory
  type: reference
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Canonical anchor for scan 2 work. Always read this BEFORE doing anything on
scan 2 — the test suite is forbidden until Step 5.

Full detail lives in **`docs/scan2_build_order.md`** (in the repo). Steps in
brief:

1. **Conditional bisection in `channel_epoch_scan.py`** — yearly → monthly →
   day-level where transitions detected; cheap stations stay cheap.
2. **Size-distribution signal** — per `(sta, source_kind, epoch)` p10/p50/p90
   as Bayesian prior alongside epoch data; tiebreaker when epoch unknown.
3. **Plan generator integration** — `plan_generator.py` becomes the single
   decision authority. Plan YAML gains `source_priority[CHN]`, `size_prior`,
   `multi_variant_resolution` per epoch.
4. **`coverage_planner.py` (NEW)** — per-channel-per-minute selection
   consuming the plan + manifest. Replaces `cross_source.select_files_for_day`
   as primary path; cross_source stays as legacy fallback.
5. **`phase3_driver.py` integration** — call `coverage_planner.plan_day()`;
   engines accept channel-restricted file lists and extract only assigned
   channels.
6. **Test suite** (only now meaningful) — 22-day source archive covering
   every category; aggregate verdicts give go/no-go for scan 2.

**Project-wide architectural change**: the old "default to disk" rule is
replaced by "use disk if disk has the channel, else telemetry". Channel
availability comes from epoch lookups, not file size or filename. File size
keeps a role as a Bayesian prior, not a determining signal.

**What's done as of compaction (2026-06-21)**:
- All artefact-producing scripts written and outputs in repo
  (`metadata/source_stats/<STA>.json`, `_discovery/`, `_epochs/`,
  `_unmatched/`, `_probes/`)
- 5 test-env days seeded
- Network surveys completed for all 41 VW stations
- Steps 1-6 NOT YET STARTED — only the diagnostic input data is ready

**What's currently running (background)**:
- `count_underscore_tele.py` on staging VM — counts underscore-tele files
  per VW station. Output: bash task `btkdinwzu` + on-VM stdout file
  `/home/.../test_env_classb/logs/count_underscore_tele.py`. When it
  finishes, the per-station numbers say which stations need the
  underscore-tele filename grammar added to `level1.py`.

**Don't**: add more days to the test source archive; build more diagnostic
scripts; re-run the network discovery sweeps (the outputs are committed and
durable).

Linked: [[reference-source-stats-tooling]] (the diagnostic scripts),
[[project-class-a-vs-class-b-framing]] (the bug classes we're addressing),
[[project-rt130-borehole-z12-mapping]] (RT130 channel-naming decision).

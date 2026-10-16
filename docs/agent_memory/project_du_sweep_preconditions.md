---
name: project-du-sweep-preconditions
description: "Things that MUST be reviewed before the DU network sweep launches. Currently: re-examine the PiesMo HHZ-stub defer_conversion rationale (PiesMo only appears in DU), and confirm LT-direct-seedlink overlap doesn't bite the override gate."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**Don't launch a DU sweep before these are reviewed.**

## 1. PiesMo HHZ-stub `defer_conversion` rationale needs re-examination

The only station-level skip in the simplified pipeline is `defer_conversion`,
triggered when `recorder_types` in `station_registry.yaml` includes `piesmo`.
The current rationale (CLAUDE.md + `plan_generator.py:DEFER_CONVERSION_RECORDERS`):
> PiesMo only sends a sporadic HHZ-only telemetry stub to EqServer
> (~250–300 files/day); the bulk 3-component data reaches SeisComP via
> separate paths (direct seedlink / SD-card via disk_to_sds). Converting
> the EqServer stub would create misleading partial records, so PiesMo
> stations are flagged defer_conversion.

**This rationale was never validated for VW** — no VW station has
`recorder_types: [piesmo]`, so `defer_conversion` never fires for VW and
the VW sweep can't tell us whether the gate is doing the right thing.

**Before the DU sweep:**
- Confirm the PiesMo HHZ-stub characterization against the actual archive
  (per CLAUDE.md "PiesMo cohort" section: 15 SAA stations, mostly DU,
  EqServer presence ~250-300 HHZ-only files/day).
- Confirm the rationale "bulk data reaches SeisComP via separate paths" —
  is the direct-seedlink already in LT, or is it pending?
- Decide whether `defer_conversion` is still the right move, or whether
  we should convert the HHZ stub knowing it's partial (under the
  "convert what's on disc" rule, partial-but-real is fine — see
  [[feedback-convert-what-is-on-disc]]). The PiesMo case might actually
  fall under that rule and not warrant a skip.

## 2. LT overlap with direct seedlink for DU

DU is ~70% migrated to the new SeisComP server (per
[[du-migration-status]] memory). That means LT has substantial
direct-seedlink data for recently-active DU stations. The override gate
in `apply.py` (`if lt_samples >= st_samples: skip`) handles this
correctly day-by-day — but at scale across many DU stations the
held.jsonl is likely to be much longer than the eqserver+VW case.

Before DU launch:
- Estimate held.jsonl size from a sample station (run apply.py
  --mode decide WITHOUT --commit on a known-overlapping DU station,
  count overrides).
- Decide held.jsonl review workflow before launch, not after.

## 3. Multi-unit pipeline test must have passed first

The DU sweep is bigger than VW (DU has more stations per the registry).
Whatever multi-unit test harness we build for VW must have passed before
DU. See [[feedback-test-the-pipeline-not-just-the-unit]].

## 4. Engine provenance — `engine_git` source-dict field MUST land before DU

The 2026-06-01 engine-provenance incident (see
[[project-engine-provenance-incident-2026-06-01]]) closed the worst of
the gap by pinning the disk_to_sds engine at `9a3b2ae` in CLAUDE.md.
But the events.jsonl source dict only records `project_git` for
eqserver; it does NOT yet record the engine SHA.

**Before DU launch:**
- Bump apply.py source dict schema to include `engine_git`.
- phase3 run-manifest emitter stamps the engine SHA from
  `git -C <disk_to_sds> rev-parse HEAD` at run start.
- Until the VM reconcile (Item 6) lands, the value should be HARDCODED
  to the known-good `9a3b2ae` to avoid reading the stale VM HEAD.
- VW BEST 2019 retry is the natural first test of this code path —
  same change lands there.

## 5. sudspy versioning — `sudspy_git` field too

The other half of the engine (the `diag`/`strict=False` tolerant SUDS
reader that the recovery QC depends on) lives in
`/Users/DSAND/projects/SubSurfObs/sudspy/` — which on the staging VM
has **no `.git` at all**. The recovery behaviour the QC relies on lives
in an unversioned copy.

**Before DU launch — two options:**
- a) Get sudspy a proper git checkout and commit the `diag`/`strict`
     changes (coordinate with sudspy owner; ideal but not in this
     project's control).
- b) Pragmatic fallback: stamp `sudspy_git: "unversioned_at_<bytes_sha>"`
     in the source dict. Surfaces the gap per-byte; doesn't block DU.

Recommendation: (b) if (a) isn't done by DU time.

## 6. VM `disk_to_sds` checkout reconcile

The 2026-06-01 incident also flagged that the staging VM's
`disk_to_sds` checkout is 14 commits stale (`014428d`) and the engine
file lives there as an untracked side-load. The reconcile is documented
in `handoffs/disk_to_sds/2026-06-01_engine-provenance/02_reply_from_eqserver.md`.

**Forcing function:** this MUST be done before BEST 2019 retry,
which is the next thing that spawns a fresh phase3. Otherwise the
retry imports the still-untracked file, and the new `engine_git`
field would either read the stale HEAD (wrong) or be hardcoded
(temporary but fine). After reconcile, the field can be auto-read.

`sweep_status.py` surfaces this as a prerequisite banner whenever
the retry list is non-empty.

# Workflow v3 — implementation strategy for the DU era

**Status: PROPOSAL (2026-10-17), for Codex review then Dan's decision.**
Builds on `docs/workflow_consolidation_architecture.md` (locked 5-stage
vocabulary — unchanged here) and the QA campaign's empirical findings
(`docs/qa/verification_campaign_plan.md`, results in flight). Scope: how to
simplify and harden the chain before the DU sweep, the performance
program, and VM sizing. The stage model itself is NOT revisited.

## What the QA campaign taught us about the architecture

The headline (uniform stratum, n=1,750 days): **~1.0% of channel-time
missing from v1 LT** (0.38% vs preferred source + 0.63% telemetry-only),
median day loses nothing, and the loss concentrates in ~2% of days in a
handful of station-years. Two structural lessons:

1. **Every confirmed defect class lives in *selection*, not conversion.**
   Sample fidelity is essentially perfect (tens of millions of samples per
   day, zero mismatches on clean days). What goes wrong is *which files*
   get converted: dual time-shifted telemetry populations (STBK B2
   pattern), disk-CHZ-only eras where telemetry had N/E (Class 2, DDNE
   ~18 ks/channel/day), hourly-bundle grammars never parsed. Selection is
   currently implicit behaviour smeared across `cross_source.py`, the
   phase3 engine branches, and the plan's advisory hints.
2. **v1 does NOT need a full resweep.** Remediation is a targeted patch
   list (worst station-years + boundary-stitch pass), derived mechanically
   from QA results. This changes the DU-era priority: the workflow work
   should serve DU correctness and operator simplicity, not a VW redo.

## Principle: one declarative chain from the registry down

Dan's framing, adopted as the design rule: **`station_registry.yaml` is
the root source of truth for what gets mapped and what doesn't**, and
every derived store must be (a) regenerable from its upstream, (b)
explicit about its lineage, (c) read through one shared loader.

```
station_registry.yaml  (root: include, network, recorder_types, location)
      │  one loader: scan/lib/registry.py (NEW) — same include semantics
      │  everywhere (fixes level1 'unknown'-is-truthy vs plan_generator
      │  'is True' divergence)
      ▼
station_dbs/<NET>.<STA>.db        MANIFEST (Stage 1) — one canonical dir
      ▼                            per network + db_manifest.csv (NEW)
metadata/source_stats/**          PROFILE (Stage 2) — diagnostic layer
      ▼
plans/<NET>/<NET>.<STA>.plan.yaml PLAN (Stage 3) — the reviewed contract
   + selection/<NET>.<STA>.selection.jsonl (NEW — see below)
      ▼
staging SDS + run manifest        CONVERT (Stage 4)
      ▼
LT + ledger                       PROMOTE (Stage 5, dev1)
```

## Proposed changes, ranked by value-to-risk

### P1 — Selection-as-data (implements build-order Steps 4 AND 5, gated by Step 6)

Build `coverage_planner.py` per `docs/scan2_build_order.md` Step 4 — the
per-(channel, minute) selector emitting `(source_path, channels_to_use,
time_window)` — and materialise its output as a selection file alongside
the plan. Revised per Codex review 2026-10-17:

- **Key = full target stream identity** `(net, sta, loc, chan)` + source
  component + epoch — NOT (station, day, channel). Required for DU
  multi-location/multi-instrument stations (HML1/RNDA; EchoPro-cohort
  loc ≠ `00`).
- **One atomic Stage-3 contract, fail-closed.** Plan + selection are
  generated together; the plan embeds the selection digest, and the
  selection header carries plan SHA, manifest content identity,
  generator repo SHA, full date range, record count, and an end marker.
  Stage 4 **hard-fails** on digest mismatch, truncation, a missing
  in-scope day, or a candidate file outside the manifest identity.
  `cross_source.select_files_for_day` survives only for plans explicitly
  marked legacy — never as a silent fallback for an absent record.
- **B2-safe selector rule** (per `scan2_planning_progress.md`): stable
  population identity (source_kind × grammar variant), scored per
  channel per day by measured coverage/continuity (not file count — the
  STBK `tele_noss` case shows more files ≠ better coverage); one variant
  per channel per day, with gap-fill allowed across populations only
  into *measured* gaps and never alternating overlapping time-shifted
  populations. Per-minute stitching stays (the CLIF ~38% partial-coverage
  case needs it); the no-alternation guard is the new constraint.
- **Step 5 is in scope, not "consumption":** the engines must honor
  `channels_to_use` (extract only assigned channels per file) and merge
  per channel — without this, Class 2 is NOT closed (taking telemetry
  N/E from a 3-channel file must not also ingest its Z over disk Z).
- **Step 6 is the go/no-go gate:** the 22-day test catalogue, verified
  with `qa_verify` against source, plus three named acceptance days —
  WLSH Class-2 day (recovers N/E), STBK 2022-10-23 B2 canonical day (one
  coherent timeline, no 1.6 s alternation gaps), CLIF
  partial-coverage day (genuine gaps still cross-source filled). DU
  launches only after Step 6 passes.
- **QA audits the contract AND the source**: `qa_verify` gains a
  selection-aware mode (LT == selection; union − selection =
  selection_loss) but keeps the current source-vs-LT mode — auditing
  only the artefact would prove compliance with a stale file, not
  correctness. Staleness is prevented by the manifest-identity binding.

### P2 — `eq2sds` CLI + renames + preflight (operator surface)

As per the consolidation doc, now concretised:

- `scan/eq2sds.py` subcommands: `manifest | profile | plan | convert |
  promote | qa | status` mapping to the locked vocabulary; file renames
  (`level1→manifest`, `diagnose→profile`, `plan_generator→plan`,
  `phase3_driver→convert`) in the same pass.
- **Every subcommand runs the SHA preflight first**: new
  `config/pinned_repo_shas.yaml` + `scan/lib/preflight.py` (rev-parse +
  porcelain + expected-SHA compare + record into run manifest). This
  retires the side-load incident class and the "owed pin decision"
  becomes a one-line config edit.
- `eq2sds qa --unit VW.BEST.2022 --sample 5` = the promote gate (below).

### P3 — QA spot-check as a promote gate (smoke check, not completeness proof)

For DU: after CONVERT of each unit, run the QA comparator on a
**deterministic risk-stratified sample** — multi-population days,
channel-epoch transition days, partial-disk days, grammar-revival days,
and both year boundaries — before PROMOTE. The gate **fails or holds on
any unexplained positive `selection_loss`, `missing_channel`,
`missing_*`, `sample_mismatch`, read errors, or contract/lineage
mismatch** (selection defects are the measured loss mode; a gate that
ignores them gates nothing). A clean sample is a smoke check: with
defects on ~2% of uniform days, 5 random days have <10% detection power,
which is why the sample is risk-stratified and why the gate complements
— not replaces — the Step-6 suite and periodic campaign audits.

### P4 — State-store hygiene (smallest useful version)

NOT a rewrite of the battle-tested queue jsonl. Three contained fixes:
- `sweep_status.py --unit-table` becomes the **only** sanctioned reader
  (already policy; enforce by making `eq2sds status` the CLI face of it).
- One canonical station-DB directory per network with a checked-in
  `db_manifest.csv` (name, size, mtime, SHA of schema) — fixes the
  current fragmentation (VW DBs only in the provenance snapshot; HOLS.db
  missing entirely; home dir has a broken stub).
- Queue dirs gain a one-line `README` + the jsonl backup files move to a
  `backups/` subdir (reduce operator misreads).

### P5 — Performance program (sandbox + ranked candidates)

**Target: 50% wall-clock reduction is a stretch HYPOTHESIS to test, not a
commitment** — the measured record (PERFORMANCE.md) says the obvious
single-VM levers are mostly spent: `python-isal` is **already deployed**
(Exp 8: all recorded EchoPro rates are post-isal) and the worker sweep
(Exp 3) found the knee at w≈4–8 with only ~25% gain from 4→16; profiling
puts 94–98% of cold time in NFS reads. Candidates, re-ranked against the
record:

| # | Candidate | Expected effect | Basis |
|---|---|---|---|
| 1 | **Two-VM year-partitioned conversion** (PERFORMANCE.md Exp 10, designed, untested) | est. 1.5–2× wall-clock; the only recorded ≥50%-class lever | needs 2nd VM + NFS whitelist (~1 day lead) |
| 2 | Threaded NFS prefetch feeding decode workers | plausible but unmeasured; attacks the actual (NFS-latency) bottleneck | read-whole-zip precedent (2× at w=1) |
| 3 | Minimus/per-channel cohort read strategy (flat at all worker counts — something other than day-parallelism binds it; diagnose, then fix) | unknown until profiled | Exp 3 DDBE flatline |
| 4 | Skip re-decode on resume (day-output fingerprint) | big on re-runs only | resume-by-default design |
| 5 | Worker topology re-tune after resize | ≤25% per Exp 3; re-measure, don't assume | Exp 3 |

**Local-disk corpus (created 2026-10-17, operator-requested):**
`~/perf_corpus/` on the staging VM — 928 MB, 20,319 files, 8 days
mirroring the origin layout (BEST 2022-06-15 EchoPro clean, HOLS
2018-06-15 EchoPro baseline, MOE3 2012-06-24 Echo, STBK 2022-10-23 B2
canonical, STBK 2023-01-08 gecko clean, DDWB 2021-06-15 Minimus, SGWU
2021-01-03 RT130, WLSH 2019-06-15 Class-2), each with the previous day's
final-minute tail files; frozen via `corpus_md5.txt`. Engine A/B timings
run against this copy (pure compute, no NFS/cache variance); NFS-path
timings are a separate labelled series.

**Sandbox protocol** (`/mnt/seiscomp_staging/perf_bench/<date>/`):
- Catalogue of ~10 representative days (EchoPro clean / EchoPro multi-SS /
  Echo / Gecko / Gecko dual-population / Minimus / RT130), fixed in a
  committed YAML.
- Each variant runs the REAL Stage-4 path into a sandboxed staging root;
  **outputs must be byte-equal to baseline** (or the diff explained and
  approved) before a timing is accepted — a speedup that changes bytes is
  a defect, not a win.
- Cold vs hot NFS cache explicitly controlled and labelled per
  [[feedback-cold-cache-discipline]]; timings recorded into
  `PERFORMANCE.md`.
- **No benchmarking while the QA sweep (or any production run) shares the
  VM** — contended timings are invalid. First baseline runs after the QA
  sweep completes.

### P6 — DU-specific gates (unchanged policy, now enforced in code)

- Channel-code resolution order CSV → VIP → FDSN → LT → BLOCKED, no
  Gecko-by-rate fallback for DU (plan gate hard-refuses).
- PiesMo `defer_conversion` re-examined per
  [[project-du-sweep-preconditions]] before launch.
- The 7 owed decisions in `docs/du_pre_sweep_notes.md` remain operator
  items; the workflow work above doesn't block on them and vice versa.

## VM sizing (measured 2026-10-17, for the scale-up decision)

**The staging VM is currently 4 vCPU / 15 GiB RAM / 2 GiB swap** (AMD
EPYC 7763 host) — much smaller than the 16-proc machine the June
documentation assumed; it appears to have been downsized during the
pause. Under the QA sweep (10 workers) it runs at load ~10.5 with swap
fully consumed — functional because the work is NFS-bound, but
memory-tight (minimus days peak several hundred MB/worker).

Recommendation for the production DU sweep (revised per Codex F5 —
the Exp 3 worker sweep caps single-VM core value):
- **RAM is the valuable dimension, not cores.** Size memory from
  *measured* peak worker RSS × chosen concurrency + ≥4 GiB headroom
  (measure during the sandbox baseline); the current swap-exhaustion is
  the real constraint. Cores beyond ~8 bought ~nothing in Exp 3
  (conversion is NFS-bound); 8–16 vCPU is ample for one VM.
- The ≥50%-class throughput lever is **a second VM partitioned by year**
  (Exp 10), not a bigger single VM — decide after the sandbox baseline.
- Re-measure at the new size before fixing worker counts; all 2026-05
  measurements predate the resize. One benchmark series per
  code+hardware configuration, never spanning a resize.

## Sequencing (revised per Codex F7 — one variable at a time)

Two branches, converging (operator framing 2026-10-17): **Branch A** =
current engine, being audited by the QA campaign and used for nothing
new; **Branch B** = v3 (selection-as-data + perf variants), built in the
sandbox and adopted as the single branch only after the Step-6 gate.

1. QA sweep completes → triage + findings doc + v1/VX patch list (Mon).
2. **Preflight first, alone** (`config/pinned_repo_shas.yaml` +
   `scan/lib/preflight.py`) — no renames in the same change.
3. Thin `eq2sds` wrappers over EXISTING filenames (renames deferred to a
   separate mechanical commit after Step 6, per CLAUDE.md's
   "non-functional renaming pass").
4. P4 DB hygiene (canonical dirs, db_manifest, rebuild HOLS.db).
5. P1 = build-order Steps 4 → 5 → 6 in order, with the three acceptance
   days; no interleaving with perf changes.
6. P5 sandbox baseline on the post-resize VM (one labelled series), then
   variants one at a time; two-VM decision from the data.
7. P3 promote gate wiring → DU launch per du_pre_sweep_notes once the 7
   owed decisions are made — **after** Step 6 passes, each decision with
   an explicit plan/preflight hook (year floor, multi-location rows,
   historical-decimation exclusions, zero-info disposition, held-count
   workflow), fail-closed.

## Out of scope

- Re-architecting engine internals (gecko/minimus/echopro branches)
  beyond consuming the selection file.
- Ledger / apply.py changes (contract unchanged).
- The suds_convert → sds_writer split (cross-repo, still queued in
  [[project-split-suds-convert-followup]]).

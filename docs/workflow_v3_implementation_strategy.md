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

### P1 — Selection-as-data (the structural fix; absorbs v2 Step 4)

Build `coverage_planner.py` (Stage 3 extension) to emit, per (station,
day, channel): **which population/files are selected**, date-aware against
`channel_epochs` — written as `selection/<NET>.<STA>.selection.jsonl`
alongside the plan. Then:

- **CONVERT consumes the selection file mechanically** (phase3 stops
  making per-day selection decisions; `cross_source.select_files_for_day`
  becomes the fallback only for days absent from the file).
- **QA audits against it**: `qa_verify` gains a mode that checks LT ==
  selection ∪ (union − selection labelled selection_loss). The auditor
  and converter then share a *reviewable artefact*, not code.
- Closes Class 2 (planner picks telemetry N/E where disk is Z-only) and
  B2 (planner picks ONE population per day instead of alternating) in one
  mechanism, with the WLSH acceptance test already defined in STATUS.md.

This is the single highest-value change: it converts the defect-bearing
logic from emergent behaviour into a human-reviewable, QA-checkable file,
exactly like the plan gate did for channel codes.

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

### P3 — QA spot-check as a promote gate

For DU: after CONVERT of each unit, run the QA comparator on a small
sample (e.g. 5 days/unit incl. Jan 1) **before** PROMOTE; refuse promote
on sample_mismatch/missing_day findings. Cost at measured rates: ~1 min
per unit — negligible against conversion time. The weekend campaign
becomes a standing per-unit gate instead of a one-off audit.

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

**Target: ≥50% wall-clock reduction on conversion.** Candidates, ranked
by expected yield over effort:

| # | Candidate | Expected effect | Basis |
|---|---|---|---|
| 1 | `python-isal` (igzip) for `.gz` inflate in sudspy | 2–3× faster inflate; EchoPro days are inflate-dominated → plausibly 40–60% day wall-time | CLAUDE.md perf section (documented, never implemented) |
| 2 | Worker topology on a resized VM (see sizing below) | linear-ish until NFS saturates; current 16-worker config assumed a 16+-core VM | PERFORMANCE.md measurements |
| 3 | Batch NFS reads: per-day file prefetch via thread pool feeding the decode worker | hides NFS latency on minute-file cohorts (gecko/minimus worst) | gecko read benchmark precedent (2× from read-whole-zip) |
| 4 | Skip re-decode on resume: day-level output fingerprint check before reading sources | big on recovery/re-runs only | resume-by-default design |
| 5 | STEIM2 encode reclen/buffering tuning | minor (<10%) | encode is libmseed C already |

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

Recommendation for the production DU sweep:
- **16 vCPU / 64 GiB** restores the design assumption (`workers_outer:
  16`) with comfortable per-worker memory; 32 vCPU only pays if NFS
  proves not to be the ceiling (test via P5 sandbox at 8/16/24 workers).
- Keep ≥4 GiB headroom over worker peak RSS — swap-thrash on CIFS writes
  is the failure mode to avoid.
- Re-measure NFS throughput at the new size before fixing worker counts;
  the 2026-05 measurements (~4,500 files/s/worker, 7 workers no collapse)
  predate the resize.

## Sequencing (proposed)

1. QA sweep completes → triage + findings doc + v1 patch list (Mon).
2. P2 preflight + P4 DB hygiene (small, immediate safety wins).
3. P1 coverage_planner + WLSH acceptance test (the Class-2/B2 fix).
4. P5 sandbox baseline + isal variant (can interleave with P1; VM resize
   decision lands here).
5. P3 promote gate wiring; then DU launch per du_pre_sweep_notes once the
   7 decisions are made.

## Out of scope

- Re-architecting engine internals (gecko/minimus/echopro branches)
  beyond consuming the selection file.
- Ledger / apply.py changes (contract unchanged).
- The suds_convert → sds_writer split (cross-repo, still queued in
  [[project-split-suds-convert-followup]]).

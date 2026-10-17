# Project 2 — the pipeline rework (single tracking document)

**Status: ACTIVE, the main effort for the week of 2026-10-17 → ~10-24.**
This document AMALGAMATES and supersedes-as-index:
`docs/workflow_v3_implementation_strategy.md` (architecture detail),
`docs/packaging_design.md` (design rules only; packaging itself parked),
`reviews/2026-10-17_workflow_v3_strategy.md` and
`reviews/2026-10-17_speedup_code_review.md` (the accepted findings), and
PERFORMANCE.md Exp 11. Track progress HERE; the others are reference.

## The deal (operator framing, 2026-10-17)

The existing pipeline is the **fallback**: audit-validated at 0.98% loss,
usable for DU as-is if the rework disappoints. The rework has **up to a
week of build-and-test** to prove it (a) fails less, (b) is faster, (c) is
simpler. **Proof = replication**: re-convert already-converted stations
and let the QA auditor judge the new output against both the originals
and the v1 output. If it delivers, DU converts on the rework — and the
replication runs double as the VW/VX repairs.

## What "better" means (acceptance gates)

1. **Fails less:** on the 22-day test catalogue + three named days —
   WLSH Class-2 day (recovers telemetry N/E), STBK 2022-10-23 (one
   coherent timeline, no 1.6 s population-alternation gaps), a CLIF
   partial-coverage day (genuine gaps still cross-source filled) —
   audited loss strictly below the existing pipeline's, zero sample
   mismatches, zero regressions elsewhere on the catalogue.
2. **Faster:** measured on the production-path benchmark (not the
   auditor's readers), same hardware, labelled cache states, cold NFS
   being the number that counts.
3. **Simpler:** one command per stage; selection decisions in a
   reviewable file; preflight on every run; fewer state stores.

## Settled architecture decision (operator, 2026-10-17 evening)

**Registry and plan collapse into one human-owned contract.** The plan
stage is NOT fundamentally different from the registry — it was mixing
identity (human territory) with scan summaries and selection hints
(machine territory). New shape:

- **REGISTRY (human, iterative with scan):** the single reviewed artifact.
  Absorbs per-station identity, target network/location, channel-naming
  basis, confirmed epochs, and the data-philosophy rule (velocity
  seismometer channels are the product; accelerometer + triggered data
  excluded). Maintained via a scan↔registry diff loop: scan proposes
  discovered epochs/rates, operator confirms or corrects INTO the
  registry. Registry tooling (the diff/confirm tool) is first-class
  Project-2 work.
- **PLAN (machine, no approval):** degenerates to the generated
  selection — which files feed conversion, per day/channel, derived from
  registry + scan, provenance-hashed (registry entry + selection replace
  the old plan-YAML policy_sha in the ledger contract). Nobody signs it.
- **Escalation replaces routine review:** anything the scan finds that
  contradicts the registry (unseen rate, unmappable channel label, DU
  station without a naming authority) stops that station and returns as
  a registry question. Fail closed, but only on genuine contradictions.
- **The auditor is the back-stop:** automatic source-vs-output checking
  before promote, instead of human pre-approval.

## Build order (branch `workflow-v3`, VM clone `eqserver_2_seiscomp_v3`)

1. **Production-path benchmark harness** (efficiency review F7) — phase
   timers around fetch/decode/consolidate/write on the 8-day local
   corpus + a cold-NFS pair. Everything after is measured against this.
2. **The chooser** (build-order Steps 4→5, the heart of "fails less"):
   coverage_planner emits per-(net,sta,loc,chan) day selections as a
   fail-closed contract bound to the plan + manifest; engines honour
   per-file channel assignments; population scoring by measured
   coverage, no-alternation gap-fill guard.
3. **Staging-prefetch experiment** (efficiency review F1 + operator's
   bulk-copy idea, the heart of "faster"): bounded producer stages
   day-dirs to local scratch over 1/4/8/16 parallel streams while
   workers convert from local disk; constant total NFS queue depth;
   DDWB control pair. Plus the nconnect mount test.
4. **Smaller queued experiments** as time allows, each gated by the
   harness: rerun fingerprint fast-path (F3), one-shot decompress (F2),
   corrupt-record batch isolation (F4), cast-after-merge (F5), writer
   metadata trims (F6).
5. **Step-6 replication gate:** 22-day catalogue + the three named days,
   then 2–3 full station-year replications compared v1-vs-rework by the
   auditor. PASS → DU runs on the rework. FAIL/late → DU runs on the
   fallback, rework continues without deadline pressure.

## Design rules carried over (from the packaging draft — the draft itself is parked)

- No absolute UoM paths outside one site config; recorder/grammar
  knowledge as data tables; every stage boundary a documented file
  schema; registry YAML is the root of the chain.
- Performance tricks (staging, mount tuning) live outside the core
  engine; correctness is universal, speed is site plumbing.

## Decisions owed (operator)

- Engine pin for any production run (`20a483c` vs `ec159ea`) —
  `config/pinned_repo_shas.yaml` placeholder.
- The 7 DU pre-sweep decisions (`docs/du_pre_sweep_notes.md`) — needed
  whichever pipeline DU runs on.
- Approval of the Project-1 minor-patch list (proposed 2026-10-17).

## Progress log (newest first)

- 2026-10-17 — Document created; preflight landed + tested; benchmark
  corpus frozen (928 MB, 8 days, local disk); Exp 11 baseline measured
  (cold NFS ≈ 6× local decode); architecture + efficiency reviews done,
  all findings accepted; VM resized 16 vCPU/62 GiB; v3 clone on VM.

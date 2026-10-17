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

## PROPOSAL UNDER STRESS-TEST — registry absorbs the plan stage (NOT decided)

**Status: an idea from the 2026-10-17 architecture discussion (operator:
"that's just an idea and maybe I'm missing something"). It must be
stress-tested and Codex-reviewed before anything is built against it.
Nothing below is settled.**

The idea: the plan stage may not be fundamentally different from the
registry — it mixes identity (human territory) with scan summaries and
selection hints (machine territory). Candidate shape:

- **REGISTRY (human, iterative with scan):** the single reviewed artifact.
  Absorbs per-station identity, target network/location, channel-naming
  basis, confirmed epochs, and the data-philosophy DEFAULT (velocity
  seismometer channels are the product; accelerometer + triggered data
  excluded by default — but per-stream, per-epoch keep|exclude with
  reason and approval, NOT a silent global filter: the HML1/RNDA DU
  accelerometer question is an open operator decision, Codex F7). Maintained via a scan↔registry diff loop: scan proposes
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

**Stress-test questions (to be answered before any decision):**
1. Provenance: the ledger contract fingerprints the per-station plan YAML
   (`policy_sha`). If no plan artifact exists, what exactly gets hashed —
   a registry-entry snapshot + selection file? Does that survive audits
   of "what policy admitted this byte" years later, when the registry has
   since been edited?
2. One file, 255 entries, growing per-epoch detail: merge conflicts,
   YAML duplicate-key hazard (already bitten once), review ergonomics of
   diffs in a 5,000-line file, blast radius of one bad edit vs per-station
   files.
3. Does removing the per-station reviewable artifact weaken the
   provenance-snapshot story (plans/ are archived per sweep today)?
4. The escalation loop: what stops a flood of trivial escalations
   turning the registry confirm-loop into exactly the review burden we
   are trying to remove?
5. DU specifically: naming authority resolution (CSV→VIP→FDSN→LT) writes
   its conclusion WHERE, with what audit trail, if not a plan line?
6. Migration cost from the current plan-YAML machinery (ledger, apply.py
   policy hashing, provenance snapshots) vs the simplification gained.

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

## Push-through sizing (2026-10-17, from existing station DBs)

Remaining source: DU 61 indexed stations = 1.36 TB / 67.7 M files /
44,949 station-days; VX 18 stations = 0.57 TB / 8.8 M files / 5,193
station-days. Total ≈ 2 TB / ~50 k station-days ≈ half the completed VW
sweep's volume → ~2 weeks of continuous single-VM conversion at measured
rates. The month-long push-through is feasible on current
infrastructure; a second VM is optional acceleration. Not yet indexed:
6 DU include-true stations + any ruled-in unknowns (MANIFEST scan needed
first). Also approved: skeleton metadata archive (1 waveform file per
station-day, prefer .dmx, + all .ss sidecars; tens of GB) before
decommission; full raw mirror rejected.

## Progress log (newest first)

- 2026-10-17 — Document created; preflight landed + tested; benchmark
  corpus frozen (928 MB, 8 days, local disk); Exp 11 baseline measured
  (cold NFS ≈ 6× local decode); architecture + efficiency reviews done,
  all findings accepted; VM resized 16 vCPU/62 GiB; v3 clone on VM.

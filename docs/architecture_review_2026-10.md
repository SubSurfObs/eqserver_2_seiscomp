# Architectural review — Claude's independent assessment (2026-10-17)

**Status: one of two independent assessments (the other:
`reviews/2026-10-17_codex_architecture_assessment.md`, written without
sight of this one). Findings and recommendations here are MINE, for
arbitration by Dan after synthesis. Nothing in this document is decided.**

Scope: the whole chain — registry → scan → plan → convert → promote —
plus the state stores, provenance, QA, and the sibling-repo coupling.
Evidence base: the completed 6,775-day audit, this week's reviews, and
the repo as built.

## The system as it actually is

Human judgment enters once (registry) and again per station (plan
review). Five stages hand off through ~10 kinds of state: the registry
YAML; per-station SQLite manifests (currently split across two
locations); per-station diagnostic JSONs; plan YAMLs; four queue jsonl
files plus backup copies; per-run manifests; the ledger repo (events +
policy copies + run records); two recovery registers; dated provenance
snapshots that re-copy most of the above. Decision logic lives partly in
data (registry, plans) and partly in code (selection, band codes,
classification). The conversion engine is imported by path from a
different repo. Writes are contained by mount modes (origin ro, LT
writable only on one host) — the strongest part of the design.

## The archive reality this architecture must survive (measured, not assumed)

Any design judged here is judged against the actual clutter, all of it
observed in the real archive this week or recorded by the scans:

- **Multiple copies of the same minute is the NORM, not the exception:**
  disk + telemetry for most stations; at STBK, TWO telemetry populations
  time-shifted by 1.648 s (glitchy dispatcher duplicates) — alternating
  between them cost 406 gaps/day on a "clean" day.
- **Copies disagree in channel content:** WLSH-class days where disk
  carries only CHZ while telemetry carries Z/N/E (~85,000 s/day of N/E
  existing only in telemetry); DDNE days with ~18,000 s/channel only in
  telemetry.
- **Grammar zoo, still growing:** 3-underscore EchoPro disk, spaced
  telemetry, Gecko compact-date disk, no-SS telemetry, `00hr` HOURLY
  BUNDLES (11,332 files at BEST/DDNE/DDWK/WLSH — unparsed until
  yesterday), 1.7 M mixed-separator files (97% at RT130 stations),
  970 k `.wrno` rollover-tagged files whose filename dates are wrong on
  purpose, single-channel stubs that are junk for Gecko but ARE the data
  for Minimus, `.ss` metadata filed under a bogus 1900/01/01 date.
- **Recorder identity is not derivable from filenames:** RT130 files are
  byte-identical in grammar to Gecko; Echo vs EchoPro distinguishable
  only inside SUDS headers; DMX sidecars shadow Gecko mseed at 19
  stations and contaminated derived recorder labels.
- **The product definition is physical, not file-ish:** velocity
  seismometer channels are the data; accelerometer channels and
  triggered event files (which share filename shapes with continuous
  data except for a seconds-offset field) must be excluded.
- **Time is adversarial at the edges:** minute files straddle midnight
  by their seconds offset; bundles straddle hours; recorders power-cycle
  30–60×/day (HOLS) writing overlapping session boundaries.

A proposed architecture earns its place by stating where each of these
lives: in the registry (human identity), in scan data (measured), in the
selection layer (decided once, auditable), or rejected at escalation.
The historical failure mode is each edge case getting handled where it
was discovered — which is how decisions ended up duplicated (Finding B)
and how unparsed cohorts stayed invisible for months.

## Findings (ranked), alternatives, my recommendations

### A. State fragmentation — no single answer to "what is the state of unit X?"

Ten store kinds; answering unit state requires joining ≥4
(`sweep_status.py --unit-table` exists precisely because of this, and the
project learned twice that reading one store alone misleads:
convert_failed append-only incident; HOLS DB simply missing). State
accreted where scripts found convenient, not by design.
**Alternatives:** (1) one per-sweep event log (append-only jsonl stays —
it is CIFS-safe — but ONE file of typed events, not four + backups) with
a locally materialised state view; (2) per-sweep SQLite as system of
record (risk: SQLite on CIFS is dangerous — would have to live on VM
local disk and be itself a copy); (3) status quo + "unit-table is the
only reader" convention.
**My recommendation:** (1). Single typed event log per sweep +
one derivation tool. Migration is mechanical; jsonl semantics preserved.

### B. Decisions duplicated across layers (the audit's lesson, generalised)

Scope rule existed in two incompatible copies (found and patched);
source selection exists in three places (cross_source, engine branches,
plan hints); channel naming in two (plan basis + engine seed_band
fallback); day classification is consumed both as QC narrative and as
control flow. Every duplicated decision eventually diverges.
**Alternatives:** (1) decision-as-data: each decision computed once by
one owner, written to one artifact, consumed read-only everywhere else;
(2) shared code modules (one function, many callers) without
materialising decisions; (3) status quo.
**My recommendation:** (1) for selection and naming (they need audit
trails); (2) is sufficient for scope. This is the generalisation of the
chooser work and is, to me, the core architectural fix.

### C. Cross-repo engine coupling

Stage 4 imports `disk_to_sds/scripts/suds_convert.py` by filesystem
path. Version discipline is operational (pins, preflight) rather than
structural; it caused the side-load incident and the stale-pin
confusion, and the already-queued sds_writer split keeps slipping.
**Alternatives:** (1) move the SUDS reader + generic SDS writer into
sudspy (a real library both pipelines import; versioned releases);
(2) vendor the engine into this repo; (3) keep path-import + preflight.
**My recommendation:** (1). sudspy is already the shared, clean library;
this also executes the queued split instead of deferring it again.

### D. Implicit schema contracts

Manifest DB columns, jsonl fields and run records carry no schema
version or generator identity. Consequences observed: the hourly-bundle
patch would have silently diverged old vs new DBs (caught in review);
promote records don't say which host ran them (bit us 2026-06-04);
semantics of `convert_failed` changed over time. Readers cannot detect
staleness.
**Alternatives:** (1) every artifact carries `schema_version` +
generator SHA, readers fail closed on unknown; (2) centralised schema
registry doc only; (3) status quo.
**My recommendation:** (1); it is cheap and it converts a whole class of
silent divergence into loud errors.

### E. Classification taxonomy does two jobs

The day classifier (clean_*/partial_*/failing_*) started as control flow
and was correctly demoted (v3-OptionB: convert everything on disc), but
its outputs still thread through plans, flagged-day lists and operator
attention. QC narrative and control flow deserve different lifecycles.
**My recommendation:** classifications become pure observability
(feeding QC reports and the auditor's strata); the convert path's only
inputs are selection + readability. Deletes a conceptual layer.

### F. The human gate's placement (the registry-absorbs-plan question)

The operator's proposal — registry as the one living human contract,
plan degenerating to machine-generated selection, escalation instead of
routine approval — is under separate adversarial review
(`reviews/2026-10-17_registry_absorbs_plan_review.md`, six stress-test
questions). I won't pre-empt it here beyond my position: the *direction*
(concentrate human judgment on identity; remove routine approvals) is
right and matches the audit evidence; the open risks are provenance
fingerprinting, one-file ergonomics at 255 entries, and escalation-flood
ergonomics. Decision belongs to Dan after both reviews land.

### G. Two pipelines, one archive

eqserver and disk_to_sds duplicate SDS writing, ledger integration and
QC lenses. Finding C's library move fixes the worst of it; full
unification (sources as adapters in one ingestion framework) is the
packaging idea's core and is correctly parked until after DU.

### H. Failure containment at staging

Origin-ro and LT-single-writer are excellent. The soft spot: both
pipelines write the SAME staging tree, so one pipeline's defective run
can contaminate the other's verification window (and cleanup tooling has
already needed year-safety guards).
**Alternatives:** (1) per-source staging namespaces, merged logically at
promote; (2) status quo + ledger discipline.
**My recommendation:** (1) for the rework, cheaply: stage under
`staging/<source>/...`; promote already records source kind.

### I. What I would NOT change

The five-stage vocabulary; mount-mode trust boundaries; dry-run-by-
default and never-delete-from-LT; the held/override human gate at
promote (it is the one approval that pays for itself); append-only event
style; the registry as root of identity; resume-by-default workers; the
independent auditor.

## Process from here

Codex's independent assessment lands separately; I will synthesise the
two into one options-and-trade-offs document per finding, flag where we
agree/disagree, and Dan arbitrates. Nothing above is settled until then.

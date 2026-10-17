The workspace is mounted read-only, so I could not create `reviews/2026-10-17_registry_absorbs_plan_review.md`. No files were changed; the complete review follows.

# Review: registry-absorbs-plan proposal  (2026-10-17, reviewer: Codex)

## Verdict

FIX-THEN-PROCEED — adopt-with-conditions: adopt the identity/selection separation, but not a monolithic YAML as the approval or provenance unit.

## Findings

### F1 — blocker — Q1/Q3: provenance needs an immutable policy bundle, not two loose hashes

- Where: [docs/rework_plan.md:46](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/rework_plan.md:46), [scan/phase3_driver.py:765](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:765), [apply.py:316](/Users/DSAND/projects/SubSurfObs/sds_staging_ledger/apply.py:316), [apply.py:362](/Users/DSAND/projects/SubSurfObs/sds_staging_ledger/apply.py:362), [manifest.py:86](/Users/DSAND/projects/SubSurfObs/sds_staging_ledger/lib/manifest.py:86), [build_provenance_snapshot.sh:42](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/build_provenance_snapshot.sh:42)
- Status: verified
- Scenario: conversion uses registry revision R and selection S, but only an entry hash and selection hash are recorded separately—or one is replaced before promotion. Years later, neither hash proves the exact combination of identity, global product rules, manifest population, generator, and selection that admitted a byte. Hashing the entire registry makes an unrelated station edit change every station’s policy; hashing only one parsed entry omits global defaults, schema/rules, external DU evidence, and comments that may contain the decision basis.
- Scenario: current `apply.py` copies LT bytes and appends events before it verifies and stores the policy. A missing or changed policy therefore produces a non-zero exit only after LT and ledger mutations have already occurred. Replacing the plan with more artifacts would enlarge this failure window unless validation moves before the first copy.
- Scenario: the orchestrator and Phase 3 independently generate different run-ID formats—one includes the year, the other does not—so queue state and ledger event provenance already name the same execution differently ([orchestrate_queue.py:186](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/orchestrate_queue.py:186), [phase3_driver.py:777](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:777)).
- Answer to Q1: hash one canonical, immutable `policy_bundle.json`. It should contain hashes and schema versions for the exact station identity snapshot, global product-policy snapshot, manifest content identity, exhaustive selection artifact, generator/preflight record, output inventory, and QA receipt. Ledger events should reference the bundle hash, while the bundle names every constituent artifact.
- Answer to Q3: this strengthens rather than weakens the snapshot story if the actual contract and selection are retained content-addressably. The present sweep snapshot copies plans and run manifests but not an explicit registry snapshot or selection artifact.
- Smallest fix or check: implement a versioned bundle record; copy and verify every constituent before walking staging or writing LT; supply one run ID from the orchestrator; make run records immutable; retain the existing `policy_sha` interpretation for historical v1 events.

### F2 — blocker — Q2: one human interface is sound; one physical YAML is the wrong isolation boundary

- Where: [station_registry.yaml:1](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/metadata/station_registry.yaml:1), [check_registry.py:21](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/metadata/check_registry.py:21), [check_registry.py:42](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/metadata/check_registry.py:42), [reviews/2026-10-17_codex_architecture_assessment.md:36](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/reviews/2026-10-17_codex_architecture_assessment.md:36)
- Status: verified
- Scenario: the current registry is already 2,313 lines, 59 KB, and 255 station blocks, before any identity epochs or component mappings. A syntax error prevents every consumer from loading any station. A nested duplicate such as two `target:` keys in an epoch silently keeps the latter value.
- The current validator only recognizes two-space, lowercase field keys. It does not reject duplicate top-level station names, nested duplicate keys, wrong types, invalid enums, overlapping/gapped epochs, conflicting target streams, missing evidence, or unapproved identity. It passed the current registry, which has no `target_location` or identity-epoch entries at all.
- Git reduces the risk of permanent loss, but it does not reduce operational blast radius: every script loads the whole YAML, and a wrong but valid edit can regenerate selections for many stations.
- Answer to Q2: keep one human-facing registry view, but store canonical approval units as small per-station identity contracts. A tool can render, search, diff, and bulk-edit those contracts as one registry. This preserves the operator’s “one place to work” goal while giving per-station hashes, small reviews, independent rollback, and small merge-conflict domains.
- Smallest fix or check: use a duplicate-rejecting YAML loader at every depth plus a strict versioned schema. Validate interval ordering/non-overlap, full target-stream uniqueness, authority/evidence, and approval state. Prefer `identity/<NET>/<NET>.<STA>.yaml` as canonical storage with a generated consolidated registry view. If the monolith is retained, changes must still emit immutable per-station snapshots for execution and provenance.

### F3 — should-fix — Q4: “genuine contradiction” is undefined and current scan evidence is too noisy to drive blocking directly

- Where: [scan/channel_epoch_scan.py:23](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/channel_epoch_scan.py:23), [scan/channel_epoch_scan.py:131](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/channel_epoch_scan.py:131), [scan/channel_epoch_scan.py:287](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/channel_epoch_scan.py:287), [plans/VW/VW.WLSH.plan.yaml:35](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/plans/VW/VW.WLSH.plan.yaml:35), [plans/VW/VW.SGWU.plan.yaml:71](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/plans/VW/VW.SGWU.plan.yaml:71)
- Status: verified
- Scenario: the epoch scanner samples roughly once per year and then bisects opportunistically. Read errors are tolerated, third channel sets are marked ambiguous, and a boundary may remain hundreds of days wide—WLSH contains a 365-day unresolved window. Treating every observed difference as a contradiction floods the operator; treating sampled boundaries as confirmed dates creates false precision.
- Repository-wide, the 41 checked-in plans contain 4,146 flagged days out of 68,966, yet every plan is generator-marked `ok`. Many flags are ordinary outages or empty spans. Feeding those into escalation would recreate routine plan review at day scale and conflict with “convert what is on disc.”
- Answer to Q4: compare new scan evidence only with the last approved identity snapshot. Emit stable, deduplicated issue IDs keyed by station, affected stream/epoch, contradiction type, evidence digest, and rule version. Separate:
  - identity contradictions, which block only the affected channel/time cell;
  - new plausible epochs, which become review proposals;
  - scan/read failures, which are QA defects;
  - coverage changes/outages, which are informational.
- Smallest fix or check: define the escalation taxonomy and state machine before the diff tool: `proposed → approved | rejected | superseded`, with evidence, first/last seen, deduplication, uncertainty intervals, and minimal blocking scope. Bootstrap findings should be bulk-triaged once rather than repeatedly escalated.

### F4 — blocker — Q5: DU has two claimed human authorities and no implemented naming-resolution path

- Where: [docs/du_pre_sweep_notes.md:13](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/du_pre_sweep_notes.md:13), [docs/du_pre_sweep_notes.md:239](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/du_pre_sweep_notes.md:239), [du_station_registry/README.md:85](/Users/DSAND/projects/SubSurfObs/du_station_registry/README.md:85), [scan/plan_generator.py:236](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/plan_generator.py:236)
- Status: verified
- Scenario: the project registry says it is authoritative, while the separate `du_station_registry/du_stations.csv` is also described as canonical and final on disagreement. That CSV is explicitly a current-state snapshot: historical recorder changes remain in free-text notes. Neither structure can currently express dated multi-instrument identity cleanly.
- The documented `CSV → VIP → FDSN → LT → BLOCKED` resolver does not exist in `plan_generator.py`. The generator consumes the registry, manifest-derived rows, and optional channel-epoch JSON; it does not load CSV, VIP, StationXML, or LT naming evidence. The current registry has zero `target_location` and zero identity-epoch entries.
- Answer to Q5: the conclusion belongs in a human-approved identity epoch, not in machine selection. Each epoch needs effective bounds, source component, physical sensor/product role, full target network/location/channel, authority, evidence references with revision/hash, confidence, decision status, approver, and timestamp. The selection should only reference that approved identity.
- Smallest fix or check: choose one canonical human authority. Recommended: import a specific DU CSV git revision as evidence/proposals into per-station identity contracts; do not leave both files canonical. Implement and test the resolver on the known TPSO, HML1/RNDA, DNL/DNL2, WKA/HKER, and zero-information cases before DU conversion.

### F5 — blocker — Q6: migration is broader than replacing a YAML filename

- Where: [scan/run_production_convert.py:69](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_convert.py:69), [scan/run_production_convert.py:249](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_convert.py:249), [scan/phase3_driver.py:694](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:694), [scan/phase3_driver.py:811](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:811), [build_provenance_snapshot.sh:47](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/build_provenance_snapshot.sh:47)
- Status: verified
- Scenario: removing plan YAML now leaves production with no work-list source, station-year epoch list, recorder dispatch, location, defer decision, policy mirror, recovery input, or ledger policy file. Although Phase 3 requires `--registry`, it never loads it; identity comes from the plan. `run_production_convert.py` discovers stations by globbing plan files and derives years from plan epochs.
- Other consumers include the metadata profiler, source/LT audit, recovery register, smoke/class-B/test-environment tooling, provenance snapshot builder, and both production orchestrators. The ledger must retain compatibility with existing v1 `policy_sha` events.
- The storage saved is negligible: the 41 checked-in plans total about 271 KB. The benefit is eliminating duplicated and misleading authority, not reducing bytes or generation time.
- Answer to Q6: migration cost is medium-to-high but bounded. Stage 3 must remain in the locked vocabulary; it becomes “compile approved identity + manifest into exhaustive selection,” not “disappear.”
- Smallest fix or check: introduce a v2 reader alongside v1, migrate every plan consumer explicitly, update AGENTS/CLAUDE only after the v2 gate exists, and prove that the converter refuses a raw manifest, missing/truncated selection, hash mismatch, unapproved identity epoch, or uncovered in-scope cell. Retain old plan-policy records unchanged for historical audit.

### F6 — blocker — the auditor cannot replace human identity review and is not presently a promotion gate

- Where: [scan/qa_verify.py:474](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:474), [scan/qa_verify.py:643](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:643), [scan/qa_verify.py:652](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:652), [verification_campaign_plan.md:151](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/qa/verification_campaign_plan.md:151), [run_production_promote.py:189](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_promote.py:189)
- Status: verified
- Scenario: a DU short-period 100 Hz source is wrongly approved as `HH*` rather than `EH*`. Conversion emits internally consistent samples and IDs. The auditor derives expected location from the same registry, derives the band through the same broadband-by-rate assumption, and does not validate the instrument-code letter. It can therefore pass data that are sample-perfect but semantically misnamed.
- The current verifier is a standalone one-day CLI. Promotion does not read a QA result; it gates only on `apply.py` return/summary and `override > 0`. The proposed selection-aware mode is documented but not implemented.
- This is the most important missed failure mode: source-vs-output QA is a back-stop for data loss and corruption, not for a wrong human identity decision.
- Smallest fix or check: retain human approval for identity; make QA validate exact emitted identities against the frozen approved contract and independently check authority conflicts. Require an immutable QA receipt before promotion. Full identity/output-inventory checks should be exhaustive; risk-stratified sample comparison can remain a data-integrity smoke test, not a completeness proof.

### F7 — blocker — the proposal silently decides an unresolved DU product-scope question

- Where: [docs/rework_plan.md:46](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/rework_plan.md:46), [docs/du_pre_sweep_notes.md:187](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/du_pre_sweep_notes.md:187), [docs/du_pre_sweep_notes.md:259](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/du_pre_sweep_notes.md:259), [scan/channel_epoch_scan.py:84](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/channel_epoch_scan.py:84)
- Status: verified
- Scenario: the proposal declares accelerometers excluded, but HML1/RNDA accelerometer handling is explicitly still an operator decision. The profiler already filters to velocity channels, so adopting the proposed philosophy prematurely could make the accelerometer streams invisible before the operator decides whether they belong in the product.
- Smallest fix or check: resolve the DU product-scope decision first. Represent `keep | exclude` per physical stream and epoch, including reason and approval, rather than as an implicit global filter. Machine selection must retain rejected-candidate receipts so exclusion is auditable rather than silent.

## Checked and found OK

- The strongest case for the proposal is supported by the code. Current plans do not contain the exact identity contract described in `CLAUDE.md`; they contain copied station/network/location, recorder epochs, summaries, and hints. The generator itself says the actual file-use list is not materialized ([plan_generator.py:22](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/plan_generator.py:22)).
- The current approval gate is largely ceremonial. `plan_generator.py` assigns only `ok` or `defer_conversion` mechanically ([plan_generator.py:245](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/plan_generator.py:245)); all 41 checked-in plans are `ok`; production skips only `defer_conversion` and has no signer, approval revision, or approval timestamp ([run_production_convert.py:257](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_convert.py:257)).
- Runtime source choice is recomputed from the mutable SQLite manifest through `cross_source.select_files_for_day`, independently of the plan’s source-priority hints ([phase3_driver.py:649](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:649)). An exhaustive selection artifact consumed without fallback would be substantially stronger than the present reviewed summary.
- Human review belongs on network/location/physical-component-to-target-channel identity and explicit exclusions. Routine disk-versus-telemetry choice is appropriately machine-owned when its exact decision receipt is immutable and audited.
- Removing routine plan approval does not inherently violate the safety model. The Stage 3 PLAN gate can remain, with “valid exhaustive selection compiled from approved identity” replacing “human signed generated summary.”
- Origin-read-only, dev1-only LT writes, no-delete, committed SHA preflight, and operator-controlled production remain compatible with the proposal.
- No nit findings.

**Adopt-with-conditions — C1 per-station schema-validated identity contracts behind one registry view; C2 an immutable content-addressed policy bundle validated before LT writes; C3 exhaustive selection as the only converter input with no runtime fallback; C4 one implemented DU authority chain with dated evidence and resolved product scope; C5 typed, deduplicated, minimally scoped escalation; C6 mandatory QA and closure receipts before promotion; C7 v1-compatible ledger/orchestrator migration plus replication tests before changing the plan-gate invariant.**

## Response

Responder: Claude (Claude Code), 2026-10-17. Verdict accepted
(adopt-with-conditions); all seven conditions C1–C7 adopted into the
synthesis as the Theme-2 resolution for operator sign-off. Notably, the
operator independently arrived at F2's conclusion in conversation before
this review landed ("one YAML per station, or a multi-station YAML, or
support both — it's file management"): per-station contracts as canonical
hashed artifacts behind one registry view.

- **F1 ACCEPTED.** Policy bundle (content-addressed, every constituent
  named, validated BEFORE any LT write) replaces my two-loose-hashes
  sketch; single run-ID authority; v1 policy_sha events retained.
- **F2 ACCEPTED.** Per-station identity contracts canonical; one
  rendered registry view for the operator; duplicate-rejecting loader +
  strict schema validation regardless of storage shape.
- **F3 ACCEPTED.** Escalation taxonomy + state machine (proposed →
  approved/rejected/superseded, deduplicated, minimally scoped) must be
  designed before the diff tool; bootstrap findings bulk-triaged once.
- **F4 ACCEPTED.** One canonical DU human authority (import du_stations
  CSV at a pinned revision as evidence into identity contracts); the
  CSV→VIP→FDSN→LT resolver must actually be implemented and tested on
  TPSO/HML1/RNDA/zero-info cases before DU conversion.
- **F5 ACCEPTED.** Stage-3 PLAN stays in the locked vocabulary as
  "compile approved identity + manifest into exhaustive selection";
  v2 reader beside v1; every plan consumer migrated explicitly; converter
  refuses raw manifests, truncated selections, unapproved identity.
- **F6 ACCEPTED.** The auditor checks data, not decisions: QA gains
  identity validation against the frozen approved contract, and an
  immutable QA receipt becomes a promotion precondition. Human approval
  stays for identity.
- **F7 ACCEPTED — my error.** Writing "accelerometers excluded" as a
  global philosophy pre-decided the open HML1/RNDA DU question. Product
  scope becomes per-stream, per-epoch `keep|exclude` with reason and
  approval in the identity contract; the rework-plan wording is
  corrected in this commit. The operator's general stance (velocity
  channels are the product) remains the DEFAULT, not a silent filter.
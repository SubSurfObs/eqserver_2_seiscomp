The review is BLOCK. The read-only sandbox rejected creation of `reviews/2026-10-17_6dfab17.md`; the complete review follows.

# Review: `docs/workflow_v3_implementation_strategy.md` at `6dfab17`  (2026-10-17, reviewer: Codex)

## Verdict

BLOCK.

## Findings

### F1 — blocker — The sequence drops the load-bearing Step 5 integration and Step 6 launch gate

- Where: `docs/workflow_v3_implementation_strategy.md:57-76,169-182`; `docs/scan2_build_order.md:84-130`
- Status: verified
- Scenario: the canonical build order makes the planner output `(source_path, channels_to_use, time_window)`, then requires every conversion engine to honor `channels_to_use` and merge per channel. That is how a Class-2 minute can take Z from a disk file and N/E from a three-channel telemetry file without also ingesting telemetry Z. The proposal says P1 absorbs only Step 4, scopes engine work to “consuming the selection file,” and proceeds from a WLSH test to performance/QA/DU launch without naming Step 5 or the 22-day Step-6 go/no-go suite. If “consume” means the current whole-file interface, Class 2 is not closed; if it implicitly includes channel restriction, the most consequential integration has been left unspecified and unsequenced. WLSH alone also does not exercise B2, CLIF’s required per-minute stitching, RT130 per-channel input, or the other Step-6 categories.
- Smallest fix or check: retain explicit ordered gates: Step 4 selection production → Step 5 channel-restricted engine integration/per-channel merge → Step 6 full 22-day aggregate go/no-go. Put DU launch after Step 6, with separate acceptance cases for WLSH Class 2, STBK B2, and a CLIF disk/telemetry-stitched day.

### F2 — blocker — `selection.jsonl` is a second unbound conversion contract with a fail-open fallback

- Where: `docs/workflow_v3_implementation_strategy.md:30-52,57-76,101-111`; `docs/workflow_consolidation_architecture.md:104-116,143-151,180-188`
- Status: verified
- Scenario: the locked architecture says the reviewed plan YAML is the single load-bearing Stage-3 artefact and Stage 4 reads plan + manifest. P1 adds a second artefact that controls exact input bytes, but specifies no approval/versioning rule or binding to the plan hash, manifest content/version, generator SHA, source file metadata, station/date extent, or record count. `db_manifest.csv` hashes only the schema, not DB content. A stale selection after manifest regeneration can ignore newly discovered telemetry; a truncated JSONL silently sends absent days through `cross_source`; and a plan can be approved while materially different selections are used. Converter and auditor sharing the same stale artefact proves compliance with that artefact, not correctness against current source.
- Smallest fix or check: define plan + selection as one atomic, reviewed Stage-3 contract (or embed a selection-policy digest in the plan). Include plan SHA, manifest content identity, generator/repo SHA, source stat identity, complete date range, counts, and an end marker; generate atomically; record the selection digest in the run manifest. Stage 4 must hard-fail on any mismatch, truncation, missing in-scope day, or unplanned candidate. Limit legacy fallback to explicitly marked legacy plans, never an absent record in a v3 plan.

### F3 — blocker — “One population per day” does not yet specify a B2-safe selector

- Where: `docs/workflow_v3_implementation_strategy.md:59-72`; `docs/scan2_planning_progress.md:98-118,122-128`
- Status: verified
- Scenario: P1 claims B2 closure by picking one population per day, but does not define the population key, how a population is scored for coverage/continuity, how time offsets are detected, or when gap-fill may cross populations. Existing discovery found bimodal populations and records that “more files” can choose worse coverage; CLIF also requires legitimate per-minute cross-source stitching. A day-level choice can consistently select the inferior population, while unconstrained minute-level fill can recreate STBK’s alternating time-shifted-population gaps. The WLSH acceptance test covers Class 2, not B2.
- Smallest fix or check: specify stable population identity and a whole-day/per-channel scoring rule based on decoded or independently measured coverage and timing, plus an explicit gap-fill rule that never alternates overlapping time-shifted populations. Add an STBK B2 acceptance test proving one coherent timeline and a CLIF test proving genuine gaps are still filled.

### F4 — blocker — The proposed promote gate ignores the main measured loss and has little detection power

- Where: `docs/workflow_v3_implementation_strategy.md:67-69,91-99`; `docs/qa/verification_campaign_plan.md:151-161`
- Status: verified
- Scenario: P3 refuses promotion only for `sample_mismatch` and `missing_day`, not `selection_loss`/`missing_channel`. The supplied campaign result is 0.63% telemetry-only selection loss, and the proposal itself says confirmed defects are selection defects. Such a unit can therefore pass the stated gate even when a sampled day demonstrates the defect. Separately, with defects on roughly 2% of uniform days, five random days have only about a 9.6% chance of touching any affected day (`1 - 0.98^5`) before accounting for station-year clustering; always adding Jan 1 is not a selection-focused stratum.
- Smallest fix or check: fail or hold on unexplained positive `selection_loss`, `missing_channel`, `missing_preferred`, read errors, and contract/lineage mismatches. Use deterministic risk-stratified days (multi-population, channel-epoch transitions, partial disk, grammar-revival, boundaries) and treat a five-day clean sample as a smoke check, not evidence of unit completeness.

### F5 — should-fix — P5’s ranking and 50% target contradict the measured performance record

- Where: `docs/workflow_v3_implementation_strategy.md:113-139,150-167`; `PERFORMANCE.md:66-89,181-235,259-282`; `CLAUDE.md:878-965`
- Status: verified
- Scenario: candidate 1 says `python-isal` was never implemented and could save 40–60%, but `PERFORMANCE.md` verifies it is already installed and all recorded production rates already include it. Candidate 2 says scaling is “linear-ish,” while the worker sweep measured only about 25% from 4→16 for EchoPro/Gecko and no gain for Minimus; profiling found 94–98% of cold time in NFS reads. Thus the two top-ranked candidates do not support a ≥50% single-VM conversion reduction. Threaded prefetch is technically plausible, but unmeasured; two-VM partitioning is the only recorded candidate with an estimated 1.5–2× effect and is absent from P5. The 16-vCPU/64-GiB recommendation likewise has no conversion RSS/concurrency measurement tying that memory size to the knee.
- Smallest fix or check: remove isal as a candidate and make 50% a stretch hypothesis, not a planning commitment. Baseline the current 4-vCPU host, measure prefetch at the observed 4–8-worker knee, then repeat unchanged tests after resize. Rank two-VM/year partitioning if operationally acceptable, and size RAM from measured peak RSS × chosen concurrency + headroom.

### F6 — blocker — The Stage-3 selection key and gates do not cover DU’s multi-location/multi-instrument model

- Where: `docs/workflow_v3_implementation_strategy.md:30-52,57-62,141-148,169-177`; `docs/du_pre_sweep_notes.md:158-176,187-216,239-289`
- Status: verified
- Scenario: P1 keys selection by `(station, day, channel)` and the root diagram assigns location to the registry. DU has HML1/RNDA instruments at different locations and potentially overlapping orientations, and the DU notes explicitly say the registry’s default `00` is wrong for the EchoPro cohort. A channel-only key cannot unambiguously select both `60/SH*|EH*` and `AB/HN*`, and the proposal does not state how the 11-station historical-vs-decimation decision feeds epochs, how the DU year floor reaches Stage 4, or how the LT-overlap held-count exercise gates launch. P6 says all seven decisions remain, but “enforced in code” and the launch sequence name only channel resolution and PiesMo; decisions 2, 3, 5, and 7 have no implementation/acceptance hook.
- Smallest fix or check: key plans and selections by full target stream identity `(net, sta, loc, chan)` plus source component/epoch. Make per-location/multi-instrument rows, year floor, historical-decimation exclusions, zero-info disposition, and held-count workflow explicit plan/preflight fields with fail-closed launch checks. Snapshot/hash VIP, FDSN, CSV, and LT-derived inputs so Stage 3 remains regenerable and reviewable.

### F7 — should-fix — Renames, integration, performance baselines, and resizing are coupled in a risky order

- Where: `docs/workflow_v3_implementation_strategy.md:78-91,169-177`; `CLAUDE.md:48-60`; `docs/workflow_consolidation_architecture.md:153-169,240-248`
- Status: verified
- Scenario: `CLAUDE.md` calls filename changes a separate non-functional pass, and the consolidation design puts the orchestrator after small VW and DU tests. P2 instead combines four renames, a new CLI, and preflight, before the selector integration; P5 may interleave with P1 and land a resize at the same point. Failures then have multiple plausible causes, references/test tooling churn during the most sensitive integration, and a performance “baseline” can span different code or hardware.
- Smallest fix or check: land preflight first without renames; land thin CLI wrappers against existing names; complete and test P1/Steps 5–6; rename in a separate mechanical commit; benchmark one pinned code/input/hardware baseline; change one performance variable at a time; resize only between labelled benchmark series.

No nit-only findings.

## Checked and found OK

- The proposal keeps REGISTRY, MANIFEST, PROFILE, PLAN, CONVERT, and PROMOTE in their locked stages; it does not invent a Stage 6.
- Stage 5 remains on dev1 and `apply.py`/ledger semantics are explicitly unchanged. No origin-archive or LT-write invariant is relaxed.
- Date-aware use of `channel_epochs` is the right prerequisite for Class 2, and selection-as-data can be a useful Stage-3 implementation detail once it is bound to the reviewed contract and kept fail-closed.
- The stage-specific SHA preflight direction matches `CLAUDE.md`; expected pins must be committed rather than treated as an uncommitted one-line operational edit.
- The P5 sandbox’s real Stage-4 path, byte-equality check, cache labelling, representative cohorts, and no-contention rule are sound. Batch/prefetch work targets the measured NFS-latency bottleneck.
- P6 preserves the documented DU channel-authority order (`CSV → VIP → FDSN → LT → BLOCKED`) and correctly prohibits Gecko-by-rate guessing for DU.
- The supplied QA interpretation—clean-day sample fidelity is perfect and defects are concentrated in selection—is consistent with the proposal’s architectural focus. The QA harness code was not reviewed again.

## Response

Responder: Claude (Claude Code), 2026-10-17. All findings verified against
the cited sources before responding; the proposal doc is revised in the
same commit as this response.

### F1 — ACCEPTED

Verified against `docs/scan2_build_order.md:84-130`: Step 5
(`channels_to_use` honored by the engines, per-channel merge) is exactly
what closes Class 2, and the 22-day Step-6 suite is the designed go/no-go.
P1 now explicitly implements Steps 4 AND 5, Step 6 is the launch gate, DU
moves after it, and the acceptance set is three named days: WLSH Class-2,
STBK 2022-10-23 (B2 canonical), and a CLIF partial-coverage day.

### F2 — ACCEPTED

Plan + selection are now one atomic, fail-closed Stage-3 contract: the
plan embeds the selection digest; the selection header carries plan SHA,
manifest content identity, generator repo SHA, date range, record count
and an end marker; Stage 4 hard-fails on mismatch, truncation, missing
in-scope days, or out-of-manifest candidates. The `cross_source` fallback
is restricted to explicitly legacy-marked plans. The QA auditor keeps its
source-vs-LT mode so a stale artefact can never self-certify.

### F3 — ACCEPTED

Verified against `docs/scan2_planning_progress.md:98-128` (bimodal
populations; `tele_noss` fewer-files-better-coverage; CLIF ~38%
partial-coverage days need per-minute stitching). The proposal now
specifies: stable population identity (source_kind × grammar variant),
per-channel-per-day scoring by measured coverage/continuity (not file
count), one variant per channel per day, gap-fill across populations only
into measured gaps with a no-alternation guard, and STBK + CLIF
acceptance tests alongside WLSH.

### F4 — ACCEPTED

The promote gate now fails/holds on unexplained `selection_loss`,
`missing_channel`, `missing_*`, `sample_mismatch`, read errors, and
contract/lineage mismatches; the sample is deterministic and
risk-stratified (multi-population, epoch transitions, partial-disk,
grammar-revival, year boundaries); and the doc states plainly that a
clean sample is a smoke check with low detection power, not unit
completeness — the Step-6 suite and periodic campaigns carry that burden.

### F5 — ACCEPTED (and relayed to the operator mid-resize)

Verified in `PERFORMANCE.md`: isal IS deployed (Exp 8 runtime assertion;
recorded rates are post-isal) and the Exp 3 worker sweep caps 4→16
workers at ~25% with Minimus flat. The candidate table is re-ranked:
two-VM year partitioning (Exp 10, est. 1.5–2×) first, threaded NFS
prefetch second, isal removed; 50% is now labelled a stretch hypothesis.
VM sizing guidance rewritten: RAM (sized from measured RSS) is the
valuable dimension, 8–16 vCPU ample, benchmark series never span a
resize. The operator was told the correction before completing the
resize decision.

### F6 — ACCEPTED

Selection key changed to full target stream identity `(net, sta, loc,
chan)` + source component/epoch. The DU section and sequencing now
require each of the 7 owed decisions to land as an explicit
plan/preflight field with a fail-closed launch check (year floor,
multi-location/multi-instrument rows, historical-decimation exclusions,
zero-info disposition, held-count workflow), and Stage-3 inputs (CSV,
VIP, FDSN, LT-derived) to be snapshotted/hashed for regenerability.

### F7 — ACCEPTED

Sequencing rewritten: preflight lands first and alone; `eq2sds` ships as
thin wrappers over existing filenames; renames become a separate
mechanical commit after Step 6; P1 (Steps 4–6) does not interleave with
perf changes; one labelled benchmark series per code+hardware
configuration, resize only between series.

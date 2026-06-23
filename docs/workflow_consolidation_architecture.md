# Workflow consolidation architecture (proposal)

**Status**: design proposal, not yet built. Drafted 2026-06-23 after a full
VW MVP test exposed that the eight individually-invoked stages of the
existing workflow are operationally fiddly even though they're logically
coherent.

The goal: collapse the eight current stages into **five logical phases**
with clear data-handoff boundaries, and consolidate the chatty internal
sub-stages of the analytical phase behind a single per-station entry
point. Do this without merging the I/O-heavy phases or removing the
human review checkpoints.

## Why this matters

A fresh network sweep (e.g. the pending DU launch) currently requires
invoking ~5–8 separate scripts per station to get from "EqServer source
exists" to "ready to convert". Each script has its own CLI surface,
output location convention, and re-run semantics. The result is:

- Operator drift (forgetting which step produced what, in what order)
- Wasteful re-runs (an intermediate diagnostic re-runs even if its
  inputs are unchanged)
- Hard-to-audit state (after a partial sweep, what's been done?)
- Mistakes like the wipe-in-loop incident (2026-06-22) where a state-
  modifying convenience designed for one stage breaks when re-used by
  another

A 5-phase model gives operators (and reviewers) one bounded thing per
phase: clear inputs, clear outputs, a clear re-run rule.

## The five phases

```
─────────────────────────────────────────────────────────────
PHASE A  INVENTORY        slow, NFS-bound, parallel per-station
                          → station_dbs/<NET>.<STA>.db
─────────────────────────────────────────────────────────────
PHASE B  DIAGNOSE         fast, manifest-only (one file read per
                          (sta, kind, year) for channel epochs)
                          → metadata/source_stats/<STA>.json (+ sub-dirs)
─────────────────────────────────────────────────────────────
PHASE C  PLAN             fast, consumes B + registry + FDSN
                          → plans/<NET>.<STA>.plan.yaml
                          *** MANUAL CHECKPOINT ***
─────────────────────────────────────────────────────────────
PHASE D  CONVERT          slow, parallel per-station per-day
                          → staging_sds/<year>/<NET>/<STA>/...
─────────────────────────────────────────────────────────────
PHASE E  PROMOTE          per-station, atomic, ledger-gated
                          → /mnt/seiscomp_archive/<year>/<NET>/<STA>/...
                          + sds_staging_ledger events.jsonl
─────────────────────────────────────────────────────────────
```

## Phase boundaries (load-bearing vs intermediate)

| Boundary | Rule | Reason |
|---|---|---|
| A→B | A's output (manifest DB) is the only B input | B is pure DB query; never re-walks NFS |
| B→C | C reads B's `_epochs/<STA>.json` + `categorize_source/<STA>.json` + registry + FDSN | Plan YAML is the *single load-bearing artefact* after C |
| C→D | D reads plan + manifest only | The plan YAML is the engine's contract |
| D→E | E reads staging output + LT for comparison; writes LT atomically | The only phase that mutates LT |

After Phase C lands, the intermediate Phase B JSONs become diagnostic-only.
Phase D and E care only about the plan YAML + manifest DB. This is the
"single artefact" boundary that lets the rest of the pipeline ignore
Phase B's sub-stage clutter.

## Consolidation: what to merge, what NOT to merge

### Merge inside Phase B — one per-station entry point

Today's Phase B is **five separate scripts**: `categorize_source.py`,
`discovery_audit.py`, `investigate_unmatched.py`, `channel_epoch_scan.py`,
`size_stats.py`. They all consume the same per-station manifest DB and
write to the same output tree. Five sequential CLI invocations per
station × N stations is the operator drift surface.

**Proposed**: `scan/diagnose.py <STA>` — one script that runs all five
sub-stages in dependency order against a single open SQLite connection.
Idempotent: skips a sub-stage if its output exists and is newer than
the manifest DB.

Output layout unchanged (`metadata/source_stats/<STA>.json` +
`_epochs/<STA>.json` + `_discovery/<STA>.json` + ...). Existing tools
that read those files keep working.

Wins:
- Per-station Phase B: 5 commands → 1 command
- Re-running B after a manifest update: idempotent skips of unchanged stages
- Easier to parallelise per-station across the network (xargs -P 16 over
  diagnose.py vs nesting parallelism inside each sub-stage)

### Merge inside Phase C — plan YAML as the single artefact

Already in this direction (commit 16f039f). Phase C's plan_generator
embeds channel_epochs, source_priority, size_prior, channels_by_source_kind
into the plan YAML. After C, the intermediate `_epochs/<STA>.json`
becomes a diagnostic file, not a load-bearing one.

**No additional consolidation work needed** — current plan YAML schema
already covers this.

### Optional orchestrator: `eq2sds prepare <STA>`

A top-level command that runs A → B → C for a single station with
dependency-aware skipping. The point isn't to hide A and B from the
operator; it's to give a single "is this station ready to convert?"
command they can run repeatedly without thinking about which sub-step
they're on.

```bash
$ eq2sds prepare DDBE
[A] inventory      DDBE  station_dbs/VW.DDBE.db    skipped (newer than archive mtime)
[B] diagnose       DDBE  _epochs/DDBE.json         re-run (manifest mtime > epochs mtime)
                         _stats/DDBE.json          re-run
                         (other 3 sub-stages)      ...
[C] plan           DDBE  plans/VW.DDBE.plan.yaml   re-run
[done] DDBE is ready to convert. status=ok, defer=false.
```

### What NOT to merge

| Pair | Why not |
|---|---|
| A ↔ B | Very different I/O profiles. A is NFS-walk bound; B is local SQLite. Keeping them separate means B is re-runnable in seconds without touching NFS. |
| C ↔ D | The plan YAML is meant to be **human-reviewed before any phase3 run**. Merging removes the gate. |
| D ↔ E | E is the only LT-mutating step. Hard boundary; conflating them removes the only checkpoint between "wrote to staging" and "wrote to LT". |
| Phase B sub-stages into Phase A | Phase A is the bottleneck. Loading B's analytical work into A means re-running A whenever the analytical code changes — which is often during development. |

## Why we keep the manual checkpoint at C→D

The plan YAML is the only artefact that:

- Asserts target channel codes (especially load-bearing for DU SEED codes)
- Asserts `defer_conversion` decisions (PiesMo-style stub stations)
- Captures `source_priority` per channel per epoch (the choice
  coverage_planner will make)
- Records `sensor_assumed_broadband` flags (when no FDSN/SUDS sensor info)

Each of these is a decision a future operator should be able to look at
*before* bytes flow into LT. Removing the human review gate means
mistakes are only discovered after-the-fact.

## What this looks like operationally for DU

```bash
# Phase A — slow, parallel
xargs -P 16 -I STA eq2sds inventory STA --net DU

# Phase B — fast, parallel; one command per station via consolidated diagnose
xargs -P 8 -I STA eq2sds diagnose STA --net DU

# Phase C — fast, network-wide
eq2sds plan --net DU \
    --fdsn metadata/uploaded/du.xml \
    --registry metadata/station_registry.yaml

# *** review plans/DU.*.plan.yaml; reject defer_conversion stations
#     where appropriate, confirm target_channel codes ***

# Phase D — slow, controllable concurrency
eq2sds convert --net DU --workers 8

# Phase E — per-station, gated
eq2sds promote --net DU --sta <one-at-a-time>
```

## Re-run semantics (idempotency contract)

Each phase's re-runnability is the contract:

- **A**: idempotent at the per-(sta, year, month, day) level. Re-running
  A over the same source produces the same DB rows (file metadata
  doesn't change unless the source file does). May add new rows when new
  source files appear.
- **B**: idempotent at the per-sub-stage level. Re-running a sub-stage
  produces the same output JSON if the manifest DB hasn't changed.
- **C**: idempotent given B's output + registry + FDSN snapshot. Re-
  running produces same plan YAML.
- **D**: idempotent given plan + manifest + source. Re-running over
  identical source produces byte-equal staging output (we measured this
  on HOLS 2018-06-15 today across the pre- and post-fast_merge_split
  runs).
- **E**: idempotent on apply.py side (skip-if-already-LT-and-byte-
  equal). NEVER deletes from LT.

The C→D human checkpoint isn't enforced by code; it's an operational
discipline. The 5-phase model just makes the boundary visible.

## Testing approach

1. **Small VW batch test** — pick 5-10 stations from existing test
   catalogue, run them through the consolidated `diagnose.py` and
   confirm output JSONs are identical to today's split-stage outputs.
2. **Small DU test** — pick 3-5 DU stations, run the full A→B→C
   pipeline end-to-end, verify plan YAMLs surface DU-specific
   correctness (FDSN-derived channel codes, PiesMo defer triggers).
3. Only after both pass: build the optional `eq2sds prepare` orchestrator.

## Out of scope for this proposal

- Renaming or re-architecting Phase D's engine code (gecko/minimus/echopro
  branches, cross_source selector, write_sds). That's a separate
  modernisation track.
- Replacing `cross_source.select_files_for_day` with the
  coverage_planner per-channel selector (build_order Step 4). That's
  orthogonal; the consolidation works the same with either selector.
- Changes to apply.py / sds_staging_ledger. Phase E's contract is
  unchanged.

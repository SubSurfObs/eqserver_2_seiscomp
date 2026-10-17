## Overall judgment

Keep the safety shell; replace the decision core. The byte converter is not the architectural risk. The system’s weak point is that source selection is neither represented once nor enforced once.

## 1. Architecture as it actually is

The system has three data stores and several loosely joined control stores. The read-only EqServer NFS archive is scanned by filename into per-station SQLite databases; profiling then samples file contents and writes derived JSON into the repository; a generated per-station YAML summarizes recorder epochs and anomalies; conversion rereads the SQLite manifest, independently chooses disk/telemetry candidates per minute, invokes `disk_to_sds`/`sudspy`, and merge-writes SDS into a mutable staging tree shared with another ingest pipeline. Conversion emits run JSON and appends events to shared JSONL queues. On dev1, a separate process reads those queues and asks the ledger’s `apply.py` to decide `write | skip | override` for staged day-channels, with only dev1 able to write long-term storage; successful writes generate append-only ledger events and content-addressed copies of the plan. The principal decision points are therefore registry scope/network, filename parsing and exclusion, sampled recorder/channel inference, generated recorder epochs, runtime per-minute source selection, runtime channel rewriting, and final LT collision handling. The strongest trust boundaries are the read-only origin and dev1-only atomic LT writer; the weakest are the mutable shared staging namespace and the hand-offs among unversioned SQLite/YAML/JSON/JSONL structures. See the declared stage/host split in [AGENTS.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/AGENTS.md:16), the actual manifest schema in [scan/level1.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/level1.py:70), runtime selection in [scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:649), and the ledger boundary in [README.md](/Users/DSAND/projects/SubSurfObs/sds_staging_ledger/README.md:19).

## 2. Ranked architectural problems

### 1. The “plan” is not the executable decision authority

This is the top problem because it sits exactly where the audit found the real defects.

The design says the plan should contain exact channel mappings, confidence, basis and a hard approval status ([CLAUDE.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/CLAUDE.md:1912)). The actual generated plan mostly contains recorder spans, day classifications and advisory source hints ([scan/plan_generator.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/plan_generator.py:260)). The converter reads station/network/location/status/recorder epochs, but source choice is recomputed by `cross_source.select_files_for_day` at runtime ([scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:744), [scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:649)). Thus the plan hash proves which summary accompanied a run, not which source file supplied each output channel.

WLSH makes the mismatch concrete: the plan reports 100% clean, spans from bogus 1900, and has an empty `source_priority` despite channel evidence being present ([VW.WLSH.plan.yaml](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/plans/VW/VW.WLSH.plan.yaml:5), [VW.WLSH.plan.yaml](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/plans/VW/VW.WLSH.plan.yaml:25)). The allowed build-order document itself describes the profiling outputs as disconnected from the actual converter ([docs/scan2_build_order.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/scan2_build_order.md:9)).

Alternatives:

- Materialize an execution selection table keyed by `(station, date, time-span, target-channel)`, containing the chosen source path, usable channels, rule/version, confidence and rejected alternatives. Conversion consumes only this table. This costs storage, but SQLite is well suited and the result is exactly reproducible.
- Keep selection dynamic, but put it in one deterministic library used identically by planning, conversion and verification. Persist a decision receipt during conversion. Less storage, but reproducibility still depends on pinning the manifest and chooser version.
- Read every candidate and perform sample-level union/conflict resolution. This maximizes recovery but is substantially more expensive and makes conflicts harder to explain.

Recommendation: use the first design, with one machine-generated selection database per station-year or run. Do not put millions of minute decisions in YAML. The converter should reject raw manifest input unless a valid selection artifact accompanies it.

### 2. Human review is placed on a machine summary, while identity remains implicit

Human judgment belongs on network, station, location, physical component and target SEED-channel epochs. Instead, the current review gate asks a human to approve a large operational plan that does not contain those mappings.

Channel identity is currently reconstructed in code. The shared SUDS engine uses FDSN only if an inventory is actually passed, otherwise it falls back to band-by-rate and location `00` ([suds_convert.py](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:58)); Phase 3 does not pass an inventory and additionally rewrites every three-letter channel to `band + H + orientation` ([scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:78)). That directly conflicts with the documented DU requirement that short-period codes cannot be inferred from rate alone ([CLAUDE.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/CLAUDE.md:1889)). The DU notes already show that location, multi-instrument identity and historical channel epochs are the unresolved human questions—not disk-versus-telemetry mechanics ([docs/du_pre_sweep_notes.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/du_pre_sweep_notes.md:158), [docs/du_pre_sweep_notes.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/docs/du_pre_sweep_notes.md:259)).

Alternatives:

- Extend the monolithic station registry with dated identity epochs and explicit source-component-to-target mappings.
- Create small per-station identity contracts, separate from generated selection artifacts. Seed them from StationXML and registry facts; require human approval only for unresolved or changed epochs.
- Treat StationXML as the sole authority and maintain a small override overlay for historical gaps and known mistakes. Elegant, but insufficient where historical identity is absent or current StationXML does not represent old epochs.

Recommendation: separate an operator-owned, schema-validated identity contract from the machine-owned selection plan. A contract should contain dated network/location/channel mappings, basis, confidence and approval. Regenerate source selections automatically whenever the identity contract or manifest changes. Human review should be exception-driven: new identity, conflicting evidence, or changed mapping—not one review per routine station plan.

### 3. Run state is fragmented, and staging is not run-isolated

Current state is distributed across local SQLite DBs, repository JSON, plan YAML, run-manifest JSON, several append-only queue files, a mutable shared SDS tree and the ledger repository. The canonical sweep view must reconstruct state by joining multiple logs with precedence rules ([scan/sweep_status.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/sweep_status.py:89)). That is already a warning that there is no authoritative unit record.

More seriously, both ingest systems merge into the same staging namespace ([CLAUDE.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/CLAUDE.md:252)), while promotion is scoped by network/station/year rather than by an explicit output inventory ([scan/run_production_promote.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_promote.py:89)). This makes provenance vulnerable to stale or unrelated staged bytes: the run manifest describes one run, but the filesystem scope can contain accumulated output from several runs.

Alternatives:

- Give each run an immutable workspace: `runs/<run_id>/{inputs, identity, selection.sqlite, output_sds, output_inventory.json, verification.json, state.json}`. Promotion consumes only the recorded inventory.
- Use one transactional control-plane database for units, attempts, selections, outputs and promotion state. Strong querying and transitions, but SQLite on CIFS would be undesirable and a service database adds operations.
- Use a content-addressed artifact store and keep queues as pointers to immutable bundles. Strong provenance and deduplication, but more infrastructure than this project needs.

Recommendation: run-scoped workspaces plus an output inventory. Keep append-only queue events, but make them pointers to immutable run bundles, not the primary state themselves. Promotion should never discover files by broadly walking the shared staging station/year subtree.

### 4. Failure containment is not fail-closed at the conversion-to-promotion boundary

Phase 3 can detect a hung pool, stop early, write a partial manifest and still exit successfully ([scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:905)). `run_production_convert.py` treats return code zero as completion and queues the unit even when only some items succeeded ([scan/run_production_convert.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_convert.py:134), [scan/run_production_convert.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_convert.py:289)). Promotion gates only on `override > 0`; the dry-run `fail` count, partial-completion flag, read errors, intended date set and missing output are not blocking conditions ([scan/run_production_promote.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production_promote.py:189)).

This safely preserves successful bytes, but it conflates “some output is promotable” with “the unit is complete.”

Alternatives:

- Fail the whole unit unless every intended cell has a terminal outcome and verification passes. Simple, but one corrupt day can delay years of valid output.
- Promote verified day-channels individually while keeping the station-year open until every expected cell is converted, explicitly absent, or waived.
- Keep the current unit promotion but require manual review of every partial/failure condition. Safe but contrary to the goal of reducing routine review.

Recommendation: promote verified successes per day-channel, but never mark the station-year complete until an automatically computed closure invariant holds:

`expected = selected = converted + explicit_no_data = verified + explicit_waiver`.

Waivers should be durable, narrow and human-authored; ordinary gaps and partial source days should not require waivers.

### 5. Schemas and taxonomies are implicit, duplicated and already diverging

The actual manifest is a single `files` table with stringly typed fields ([scan/level1.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/level1.py:70)); the design document describes additional enriched and aggregate tables that are not the production contract ([CLAUDE.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/CLAUDE.md:1725)). Registry validation checks only duplicate keys, not field types, allowed values, required identity or epoch overlap ([metadata/check_registry.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/metadata/check_registry.py:26)).

Source taxonomy is repeated in regex tables across scripts. `channel_epoch_scan.py` knows `perchan_mseed` ([scan/channel_epoch_scan.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/channel_epoch_scan.py:56)), but `size_stats.py` maintains a separate list that omits it, producing “unknown source_kind” ([scan/size_stats.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/size_stats.py:35), [scan/size_stats.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/size_stats.py:71)). Recorder cohorts are also duplicated as hard-coded station sets rather than consistently derived from the registry ([scan/check_manifest.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/check_manifest.py:50)).

Alternatives:

- Build a small internal domain package containing versioned models, enums, filename grammars, recorder capabilities, migrations and validators.
- Make SQLite the domain model: versioned migrations, normalized source-kind/recorder tables and derived SQL views. Excellent for scale, less convenient for operator editing.
- Retain the scripts but add `schema_version`, strict JSON/YAML schemas and cross-stage contract tests. Cheapest interim step, but duplicated policy will continue to drift.

Recommendation: a common domain package backed by a versioned SQLite schema. YAML should be restricted to operator-owned identity. Machine outputs should carry schema version, input hashes, generator version and validation status.

### 6. Cross-repository coupling is runtime-path coupling, and obsolete entrypoints remain executable

Phase 3 imports sibling code by mutating `sys.path` to a hard-coded home-directory checkout ([scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:57), [scan/phase3_driver.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:635)). A preflight library now exists, but no production script calls it, and all configured pins are currently null ([scan/lib/preflight.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/lib/preflight.py:93), [config/pinned_repo_shas.yaml](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/config/pinned_repo_shas.yaml:17)).

There are also conflicting executable histories: `phase3_driver_v2.py` contains materially different gate semantics, and `run_production.py` can promote and delete staging in one process ([scan/run_production.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/run_production.py:100)), contrary to the canonical dev1-only promotion boundary.

Alternatives:

- Publish `sudspy` and the conversion/writer core as pinned Python packages/wheels with a lockfile; expose one supported pipeline CLI.
- Combine the three code repositories into a monorepo. This simplifies atomic changes but weakens the useful privilege/release boundary around the LT ledger.
- Keep repositories separate and communicate only through versioned subprocess/file protocols. Strong isolation, but less efficient and harder to test internally.

Recommendation: versioned installable packages for conversion libraries, while keeping `sds_staging_ledger` as a separate privileged application. Make one production CLI canonical; move obsolete drivers and orchestrators out of executable paths. Integrate preflight into that CLI so a production run cannot start without exact pins.

## 3. What I would not change

- Keep the read-only origin, staging/LT separation, dev1-only LT writes, atomic copy/rename and no-delete rule. This is the strongest part of the architecture ([AGENTS.md](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/AGENTS.md:38), [README.md](/Users/DSAND/projects/SubSurfObs/sds_staging_ledger/README.md:239)).
- Keep SQLite for raw inventory. One row per source file is the right durable substrate; enrich it through migrations rather than replacing it with YAML or pandas.
- Keep “convert what is on disc.” Partial days are facts, not workflow failures. Machine selection and verification should determine what can be preserved.
- Keep the current conversion/writer core unless selection evidence disproves it. It isolates unreadable files, preserves recovered data, performs deterministic merging and writes atomically ([suds_convert.py](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:75), [suds_convert.py](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:158)).
- Keep append-only ledger events and content-addressed policy/run records. Change what the policy represents: it should point to the exact identity and selection artifacts that produced each promoted file.
- Keep the independent byte/sample verifier. It already detects missing intervals, selection loss, phantom data and sample mismatch ([scan/qa_verify.py](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:549)); promote it from an audit campaign into an automatic run-completion gate.
- Keep human review for genuine LT overrides and identity ambiguity. Remove it from routine source-copy selection.

In one sentence: replace “human-approved station summary plus runtime heuristics” with “human-approved identity epochs plus machine-generated, immutable, verified source selections.”

I did not open the excluded architecture/rework documents or `reviews/`, and made no file changes.
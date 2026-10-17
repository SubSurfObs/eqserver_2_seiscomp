# STATUS — where the project stands

> Single "you are here" file for every agent and model. Update it at the end
> of every session. Keep it under ~150 lines; move detail into `docs/`.

**Last updated:** 2026-10-16 (evening: re-verified on the VMs — mounts, repo
SHAs, sweep state and the WLSH outcome below are live-checked, read-only).
**Branch:** `rewrite-suds2sds` is the working branch (main has not been merged).

## Scope (from `metadata/station_registry.yaml`, 255 entries)

| Category | Count | Notes |
|---|---|---|
| VW `include: true` | 41 | scan 1 converted + promoted (provenance snapshot `VW/2026-06-10`) |
| VX `include: true` | 18 | main sweep not launched, but 32 VX units (Moe/ALLA 2012 Echo work) already went through the v1 queue |
| DU `include: true` | 67 | sweep not launched; 7 decisions owed (see below) |
| `include: unknown` | 47 | 5 tentatively DU; need triage before any DU/VX sweep is "complete" |
| `include: false` | 82 | artefact/test dirs, GA/AU (ANSN), S1/AUSiS, OZ, Raspberry Shake, already-converted VX (ABM*, WPM*, RDK*), closed |

## Where each stream of work stopped (as of 2026-06-24)

1. **VW scan 1 (v1)** — done and promoted. Known LT defects catalogued in
   `docs/v1_lt_defects_and_v2_resweep_scope.md` (12 classes). Realistic
   resweep: ~30–80 station-years, driven mainly by Class 1 (side-load
   Gecko/Minimus fragmentation) and Class 2 (disk CHZ-only eras where
   telemetry has N/E).
2. **Scan 2 / v2 engine** (`docs/scan2_build_order.md`, 6 ordered steps):
   - Step 1 conditional epoch bisection — done (`3c52c50`)
   - Step 2 size stats — done (`8d729a0`)
   - Step 3 plan_generator ingests epochs + size stats — landed (`16f039f`)
     but **incomplete for Class 2** (Codex review 2026-10-16, F1): the
     per-plan-epoch `channels_by_source_kind` / `source_priority` hints
     UNION channel epochs over the whole recorder epoch, so a CHZ-only
     disk era inside a broader epoch still yields disk-first priority for
     CHE/CHN. Fix: split plan epochs at channel-epoch boundaries, OR make
     Step 4 do date-aware lookup against `channel_epochs` directly.
     Acceptance test: WLSH 2018–2023 CHE/CHN priorities must exclude
     `disk_mseed`.
   - Step 4 `coverage_planner.py` — **not started** (no file exists; plan
     YAML carries hints "coverage_planner will consume this")
   - Step 5 phase3 integration — partly: `fast_merge_split` wired into
     gecko/minimus engines (`2ade29e`); planner hookup pending
   - Step 6 test suite — test_env tooling exists; full run pending
   - **WLSH validation run — resolved 2026-10-16.** The run completed
     cleanly on 2026-06-24 15:22 (13 year-manifests, engine
     `eqserver@a53d74e`; 2012–2017 `no_files`, 2018–2024: 1,776 ok days,
     34 qc_flagged, 0 failures/timeouts). But the v2 staging output shows
     **no channel-coverage change vs v1 LT** across 2018–2023: identical
     per-channel day-file counts and per-channel directory byte totals
     agreeing to within KB (`du -sb` aggregate comparison — NOT a
     per-file hash; individual files may still differ at trace boundaries
     per the known fast_merge_split trim semantic). Recovered N/E would
     be GB-scale, so **Class 2 is NOT confirmed fixed** — as expected,
     because Step 4 `coverage_planner.py` was never built;
     the regenerated plan only gained `channel_epochs` hints and an empty
     `source_priority: {}` (uncommitted on the VM). WLSH's `_epochs` data
     shows the channel evidence comes from the `perchan_mseed` population
     (CHZ-only 2018-07→10 and 2021-07→2024-07; 3-channel 2018-11→2020-07).
     The run DID validate engine stability (clean completion, zero
     timeouts); determinism is NOT established by a single run — that
     needs two runs from identical pinned inputs with per-file
     comparison. Side findings:
     v2 names the 2018 4th channel `CHO` where v1 LT has `CDO`, and the
     2019 one-day high-rate epoch `HH*` where v1 LT has `HQ*` — naming
     deltas to adjudicate before any v2 promote; WLSH 2024 staging has
     308 days vs LT 366 (LT's extra days presumably post-migration
     seedlink).
3. **Workflow consolidation** — stage vocabulary locked (`c11c1f0`);
   `scan/diagnose.py` wrapper exists; script renames, `eq2sds` CLI and the
   per-stage SHA preflight script are TODO.
4. **DU sweep** — blocked on decisions in `docs/du_pre_sweep_notes.md`
   (TPSO network, HML1/RNDA multi-instrument, 11 multi-channel stations,
   zero-info 10, pre-2017 year floor, PiesMo defer, LT-overlap held count).
5. **VX sweep** — pending; Echo channel recognition landed in
   `disk_to_sds@94ff229` specifically for VX MOE3–8.

## Verified on the VMs 2026-10-16 (read-only session)

- **Staging VM alive and healthy** (`rs-l-0ezd3a`): all three mounts live
  with correct modes (origin NFS **ro**, LT CIFS **ro**, staging CIFS
  **rw**); `~/station_dbs/` present with **81** DBs.
- **Repo SHAs on the VM:** `eqserver_2_seiscomp@a53d74e` (1 behind Mac's
  `c16e0e9`), **dirty with the 2026-06-24 WLSH/BEST diagnose outputs +
  plan-hint updates** — pull + commit these; `disk_to_sds@ec159ea` clean
  (`94ff229` is an ancestor; newer commits are resample/SmartSolo work);
  `sudspy@1e51cd8` clean; `sds_staging_ledger@da216ea` dirty with
  cleanups.jsonl appends incl. untracked VX 2012 ALLA/MOE3 (operator
  commits these).
- **Engine pin updated in CLAUDE.md** (was stale at `94ff229`).
  `suds_convert.py` changed exactly once since then: `20a483c` (write_sds
  → `sudspy.fast_merge_split`, committed 2026-06-23, deliberate); the v2
  BEST/WLSH runs included it. The **exact repo SHA for the next Stage-4
  production run is still an owed decision** (Dan): either checkout
  `20a483c`, or validate and pin current VM HEAD `ec159ea` (clean;
  later commits are resample/SmartSolo, engine untouched). Record the
  choice in `config/pinned_repo_shas.yaml` when the preflight script is
  built. "20a483c or later" is NOT a pin.
- **Sweep state (v1 queue, raw jsonl):** 364 units terminally handled
  (VW 332 + VX 32): 357 promoted + cleaned, 7 `skip-empty` no-data
  units (BRTH 2012–2017 + CLIF 2012 — the unit-table shows these as
  "converted-not-yet-promoted" because skip-empty intentionally never
  sets promoted/cleaned). Benign; nothing pending.
- **v2 queue:** 22 units converted (BEST 2017–2025, WLSH 2012–2024),
  **none promoted**; written into the main staging tree
  `/mnt/seiscomp_staging/seiscomp_archive`.
- **dev1 preflight (read-only half):** hostname correct, LT mounted
  **rw**. Mount now authenticates via a mediaflux token rather than the
  `dsand` username. (touch+rm write test not run — read-only session.)
- Agent-memory notes: **all 50 survived**; copied into
  `docs/agent_memory/` this session.

## Housekeeping still open

- `scan/level1.py` treats `include: unknown` as in scope (truthy test) while
  `plan_generator.py` requires `include is True`. Harmless today (plan gate),
  but make them consistent.

## Next actions (in order)

- [ ] On the VM: `git pull --ff-only` then commit the 2026-06-24 diagnose
      outputs + WLSH/BEST plan updates (push to `rewrite-suds2sds`).
- [ ] Adjudicate the WLSH naming deltas (`CHO` vs `CDO` 2018, `HH*` vs
      `HQ*` 2019) before any v2 promote decision.
- [ ] Assumptions inventory session: every assumption the conversion
      relies on, and what (if anything) currently tests it → work plan
      for the testing week.
- [ ] Dan: choose the exact `disk_to_sds` SHA for the next Stage-4 run
      (`20a483c` checkout vs pin `ec159ea`) — see Codex F3.
- [ ] Build v2 Step 4 `coverage_planner.py` (the actual Class-2 fix) with
      **date-aware lookup against `channel_epochs`** (or split plan
      epochs at channel-epoch boundaries first — Codex F1), then re-run
      WLSH; acceptance test: 2018–2023 CHE/CHN priorities exclude
      `disk_mseed`, and recovered N/E appears in staging where v1 LT is
      Z-only.
- [ ] Decide sequencing (recommended: finish v2 Steps 4–6 → VX → DU → targeted VW resweep).
- [ ] Write the per-stage SHA preflight script (`config/pinned_repo_shas.yaml`).

## QA verification campaign — COMPLETE (2026-10-17 ~16:30 AEDT)

**All 6,775 sampled days audited. Headline (uniform stratum, last-record
dedup): 0.98% of channel-time missing from v1 LT** — 0.47% vs the
preferred source + 0.51% existing only in unselected telemetry. Median
day loses nothing; ~3.5% of uniform days lose >5%, clustered in a
dozen-ish station-years (worst: DDSW 2021/2018, MARD 2021, HOGN 2019,
WDSD 2018). All recoverable; **no full VW resweep needed — targeted
repair list instead.** Results:
`/mnt/seiscomp_staging/qa_verify/2026-10-16/results.jsonl` (use LAST
record per (net,sta,date); 33 benign duplicates from a stray process;
1 residual unverified HOLS day). HOLS.db rebuilt (2.4 GB,
`~/station_dbs/VW.HOLS.db`) after being found missing from the
provenance snapshot. Key confirmed defects: STBK-style B2 dual-population
alternation gaps; Class-2 selection losses (DDNE up to ~18 ks/chan/day);
legacy `DL*`/`SUM` channel codes inside v1-era LT years (LRWS/LRNW 2021,
BRIG 2020). Triage into `docs/qa/` findings doc + Codex interpretation
review still owed (Monday target).

## Workflow v3 + perf (branch `workflow-v3`, in progress 2026-10-17)

- VM resized to **16 vCPU / 62 GiB**; mounts verified post-reboot.
- Landed: SHA preflight (`scan/lib/preflight.py` +
  `config/pinned_repo_shas.yaml`, dirty/side-load/pin checks; .DS_Store
  untracked). Caught real uncommitted work in Mac's disk_to_sds
  (smartsolo script) — operator to commit.
- Benchmark corpus: `~/perf_corpus/` on VM local disk (928 MB, 8 days,
  md5-frozen; recorded in `docs/workflow_v3_implementation_strategy.md`).
- Strategy doc Codex-reviewed (BLOCK, 7 findings, all addressed:
  `reviews/2026-10-17_workflow_v3_strategy.md`). Key: engines must honor
  per-file channel restriction (Step 5) or Class 2 isn't closed; plan +
  selection = one fail-closed contract; 50% speedup is a hypothesis —
  isal already deployed, worker knee w≈4–8, NFS is 94–98% of cold time.
- Packaging track (`docs/packaging_design.md`, Codex iteration running):
  package = generic converter + built-in auditor, ends at verified
  staging SDS; UoM speed tricks stay out of it. Operator priority:
  **UoM speed first**, hypothetical other sites second.
- Speed plan (VM now idle): baseline corpus-vs-NFS on the 8 days, then
  station-year bulk-staging prefetch, then `nconnect` mount test; 2nd-VM
  year split in reserve.

## Session log (newest first, one line each)

- 2026-10-16 (evening) — Read-only re-verification on both VMs: mounts/repos/sweep state confirmed; WLSH verdict recorded (run clean, output byte-equal to v1 → Class 2 not yet fixed, coverage_planner missing); 50 agent-memory notes recovered to docs/agent_memory/.
- 2026-10-16 — Re-orientation after pause; added AGENTS.md, STATUS.md. Workflow set: Claude Code implements, Codex reviews read-only via `/codex-review` (docs/review_brief.md → reviews/). OpenCode dropped.

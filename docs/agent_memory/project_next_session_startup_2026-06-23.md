---
name: project-next-session-startup-2026-06-23
description: "Startup priorities for next session 2026-06-23 evening / 2026-06-24. In-flight: diagnose.py rerun on staging VM (PID 3480413, started ~13:11 UTC, ETA finish ~14:30 UTC). When done: pull eqserver on VM, commit source_stats outputs, rebuild test-env mini-DBs, regenerate plans, convert_all to fresh staging_sds (v3). Three staging dirs preserved for evolution tracking."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Picking up from session ending 2026-06-23 evening (context full at 2%).

## In-flight at session end

**diagnose.py rerun on staging VM** — PID 3480413, started ~13:11 UTC.
At ~16/40 stations after 34 min when context ran out. ETA finish
~14:30 UTC (~50 more min from session end). Log:
`/home/.../test_env_classb/logs/v3_diagnose_*.log`.
Background monitor task `bkr0aazrv` waits for PID to die then prints
final state.

**No other background runs.**

## Three staging dirs on VM (do NOT delete any)

```
/mnt/seiscomp_staging/test_env_classb/
├── staging_sds_premerge_baseline   v1 (yesterdays slow obspy.merge output)
├── staging_sds_v2_fastmerge         v2 (todays fast_merge_split output, byte-equal-ish to v1)
└── staging_sds                      v3 (being built — empty when diagnose finishes,
                                         then mini-DB rebuild + convert_all populates)
```

Caveat on baseline: 8 days in `staging_sds_premerge_baseline` are
actually fast_merge_split output from the killed first attempt — see
`/home/.../test_env_classb/BASELINE_OVERWRITES_2026-06-23.txt`.

## Recovery steps when session resumes

1. **Check diagnose status first.** Bash on Mac:
   ```
   ssh dsand@172.26.144.41 'ps -o etime= -p 3480413 && echo running || echo DONE'
   ```
   If still running, wait. Monitor task `bkr0aazrv` will notify on completion.

2. **After diagnose finishes:**
   ```
   # On staging VM:
   cd /home/.../eqserver_2_seiscomp
   git pull --ff-only                            # lands c11c1f0 (vocabulary doc)
   git add metadata/source_stats/                # ~220+ files updated by diagnose
   git commit -m "metadata/source_stats: Phase B re-run v3 outputs"
   git push origin rewrite-suds2sds
   ```

3. **Rebuild test-env mini-DBs** (for the 40 catalogue stations):
   ```
   for STA in BEST BRIG BRTH CLIF CRJN DDBE DDNE DDSW DDWB DDWK DEAL FORG \
              HDDL HODL HOGN HOLS KRAN LOCU LOYU LRNW LRSE LRSH LRWS MARD \
              MOSU MRDN NARR OUTU SCM2 SCMB SGWU SOMU STBK TRPU TYHS WDSD \
              WILU WLSH WPNH WPSH; do
       python3 scan/test_env_build.py rebuild $STA  # does level1 + plan_generator
   done
   ```
   (Or `xargs -P 16` parallelise. ~30 min wall-clock parallel.)

4. **convert_all to fresh staging_sds (v3):**
   ```
   python3 scan/test_env_run.py convert_all  # writes to fresh empty staging_sds
   ```
   ETA ~30 min with fast_merge_split.

5. **md5-diff** v3 staging_sds vs v2 staging_sds_v2_fastmerge to confirm
   determinism of fast_merge_split:
   ```
   python3 /home/.../test_env_classb/logs/md5_diff_staging.py
   # (script exists; will need adjustment for v3 vs v2 instead of v2 vs v1)
   ```

## Locked vocabulary (committed today)

5 executable stages + 1 manual foundation:
- Stage 0 REGISTRY (manual, station_registry.yaml + FDSN snapshots)
- Stage 1 MANIFEST (was: level1.py)
- Stage 2 PROFILE (was: diagnose.py, will rename to profile.py)
- Stage 3 PLAN (was: plan_generator.py)
- Stage 4 CONVERT (was: phase3_driver.py)
- Stage 5 PROMOTE (was: apply.py, on dev1 only — WRITE-HOST INVARIANT)

Authoritative reference: CLAUDE.md "Pipeline stages and their dependencies".
See also `docs/workflow_consolidation_architecture.md` and memory note
`project-pipeline-stages-vocabulary`.

## Key empirical findings from this session

- **Class B / side-load contamination framing was WRONG.** Empirically
  disproven on 9/9 candidates (BRTH 2020-12-18 = 33,545 LT traces is
  real Gecko firmware packet structure; DDBE/SCM2/DDNE 2018 byte-equal
  to rescan). Task #54 deleted. See memory
  `project-class-a-vs-class-b-framing` (updated 2026-06-23).
- **fast_merge_split trim semantic adopted** in all engines. 7x sweep
  speedup. ~33% of rescan day-files BYTES_DIFFER vs scan-1 LT at trace
  boundaries (deliberate semantic, not regression). See memory
  `project-fast-merge-split-trim-semantic`.
- **MVP test catalogue: 224 days, 209 ok, 0 timeouts** with fast_merge_split.

## Repo state at session end (staging VM)

| Repo | HEAD | Branch | Behind | Dirty | Notes |
|---|---|---|---|---|---|
| eqserver_2_seiscomp | ecb83f0 | rewrite-suds2sds | 1 | 235 | Mid-diagnose-run; sync after run finishes |
| sudspy | 1e51cd8 | main (now) | 0 | 0 | Branch renamed master→main today |
| disk_to_sds | 9f20d22 | main | 0 | 0 | Clean |
| sds_staging_ledger | f7888a0 | main | 0 | 31 | 31 cleanups.jsonl — operator commits |

Mac copies (eqserver, sudspy, disk_to_sds, sds_staging_ledger): all on
correct branches, clean as of session end EXCEPT eqserver still on
rewrite-suds2sds branch which is the active branch.

## Pending operator-side cleanups (NOT for Claude to touch)

- `sds_staging_ledger` 31 `cleanups.jsonl` appends — user authorises ledger commits
- Adopt sudspy branch-name convention in `sudspy/CLAUDE.md` (handoff written
  at `sudspy/handoffs/2026-06-23_branch-name-convention/01_request.md`)

## Outstanding deferred work (post-resume)

- File renames per locked vocabulary: `level1.py`→`manifest.py`,
  `diagnose.py`→`profile.py`, `plan_generator.py`→`plan.py`,
  `phase3_driver.py`→`convert.py`. Mechanical pass when there is quiet time.
- Build `eq2sds` CLI orchestrator that fronts the 5 stages.
- DU sweep prep (separate track per architecture doc Section "Operationally
  for DU"): refresh FDSN snapshot, gate `_enforce_band_instrument` off
  for DU, plan_generator FDSN-aware, PiesMo defer verification.
- Pre-flight commit-hash verification tooling (`config/pinned_repo_shas.yaml`
  + verify script) — discipline locked in CLAUDE.md, mechanism not built yet.
- Defensive `max_npts` guard in suds_convert for STBK 2012-01-01 88B-sample
  bogus header (1 day failure currently).
- Per-day retry for silent zero-byte clusters (task #51).

## Tasks state at session end

Open: #42 (DU sweep), #44 (registry coverage_end + day-skip), #48 (NFS-walk
audit), #51 (silent zero-byte retry), #55 (registry recorder_types
backfill), #56 (Class B test suite — possibly redundant after today),
#57 (test env infra), #58 (epoch-informed coverage_planner).

Closed today: #52 (fast_merge_split wire-up), #54 deleted (Class B
re-conversion — disproven).

Linked memory notes (read these for context):
[[project-pipeline-stages-vocabulary]], [[project-class-a-vs-class-b-framing]],
[[project-fast-merge-split-trim-semantic]], [[project-dmx-sidecar-pattern]],
[[feedback-no-default-wipe-in-loops]], [[feedback-verify-before-claiming]].

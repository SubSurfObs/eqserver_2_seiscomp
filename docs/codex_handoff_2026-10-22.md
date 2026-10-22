# Codex take-over handoff — 2026-10-22

**For Dan (plain English):** Claude is out for 3–4 days. This note hands the
keyboard to Codex for that window. Codex's jobs: finish the one unbuilt code
fix, watch the overnight re-test of the previously failed days, and fix
anything that re-test exposes. Nothing touches production data. When Claude
returns it reviews everything Codex did, then the big independent review
(`docs/codex_review_brief_v3.md`) runs before any DU conversion.

## Role change (operator-authorized, temporary)

AGENTS.md normally makes Codex read-only. **For this break only, Dan has
authorized Codex to implement** — write code, run tests on the VM, commit.
Everything else in AGENTS.md still binds, especially:

- **Hard invariants 1–8** (AGENTS.md). In particular: never write under
  `/mnt/eqserver_archive`; never touch `/mnt/seiscomp_archive`; no
  production convert or promote runs at all during this window — test
  directories under `~/v3_test/` and the VM clone only.
- **No new phases.** Implement only what this handoff lists. Anything that
  looks like "start DU", "promote", "repair VW/VX" needs Dan's explicit go.
- **Timestamps are converted exactly as recorded on disc — never
  "corrected".** If a test shows a timing oddity, record it; do not fix it.
- **Commit everything; git is the only transport** between Mac and VM
  (never scp). Branch: `workflow-v3` (VM clone
  `~/projects/SubSurfObs/eqserver_2_seiscomp_v3`). Update `STATUS.md` at
  the end of every session — plain English, Dan reads it.
- **Communicating with Dan:** AGENTS.md "Communicating with Dan". Plain
  English, headline first, name the branch, one recommendation not a menu.

## Where things stand (details in STATUS.md SAVE-POINT 2026-10-22)

- Fix 1 (timestamp fidelity, both weld layers) — done and proven
  (LKHRT 2023-07-28: 6.47 M bad samples → 0; KORUM byte-identical).
- Fixes 2+3 (chooser probe-union + short-file fill, commit `5313ae2`) —
  committed; single-day test result recorded in STATUS.md (TYHS
  2023-08-31; expectation: missing ≈ 0, was 3,236 s).
- Fix 4 (mixed-format per-engine dispatch, `2e760cc`) — committed, tested
  on the TYHS day.
- Overnight re-run of all previously failed study days — launch state
  recorded in STATUS.md.

## Codex's task list, in order

1. **Triage the failed-day re-run** when it finishes:
   `~/v3_test/triage_study/results.jsonl` on the VM
   (`dsand@172.26.144.41`). Count PASS/FAIL; for each remaining FAIL,
   reproduce with `bash ~/v3_test/fix_one_day.sh NET STA DATE DB PLAN`
   (station DBs: `/mnt/seiscomp_staging/eqserver_provenance/VW/2026-06-10/
   station_dbs/` for VW; `~/station_dbs/` for DU; synth plans under
   `~/v3_test/triage_study/synth_plans/`). Root-cause and fix in the
   chooser/converter; re-run the day to prove it. If a failure class looks
   like source-data reality (true gap) rather than a code defect, record it
   in `docs/qa/triage_evidence_map.md` instead of forcing a fix.
2. **Build Fix 5 — identity-driven channel naming override.** In the
   convert path, channel codes coming from the plan/identity contract must
   override `_enforce_band_instrument`'s band-by-sample-rate renaming
   (engine side lives in `disk_to_sds/scripts/suds_convert.py`; the call
   path is phase3 → engine). Needed for DU stations whose correct code is
   `EH*` at location 60 — the rate-derived band would wrongly force `HH*`.
   Must be driven by the plan YAML (an explicit per-epoch target code),
   never a hard-coded station list. Add a test day proving an `EH*` plan
   target survives to the SDS filename and headers. Do NOT guess any DU
   station's intended codes — that is Dan's identity decision; the fix is
   only the mechanism that honors whatever the plan says.
3. **If idle after 1–2:** re-run the full triage study end-to-end from
   cached selections deleted (clean re-build) and confirm the pass rate is
   stable; note timing numbers in `PERFORMANCE.md`.

## What Codex must NOT do

- No DU, VX or VW production conversion, promotion, or repairs.
- No changes to `metadata/station_registry.yaml` or any plan's identity
  content (mechanism yes, identity values no).
- No edits on branch `rewrite-suds2sds` (frozen fallback).
- Do not delete `~/v3_test/du_pilot` contents' provenance notes without
  reading them; the directory itself Dan has said may be deleted.

## When Claude returns

Claude reviews every Codex commit, then the sequence in
`docs/rework_plan.md` "Path A" resumes: closure equation + inventory
promotion build, dress rehearsal (BEST + WLSH replication, STBK
2022-10-23, CLIF acceptance days), the full independent review
(`docs/codex_review_brief_v3.md`), Dan's sign-offs, then DU.

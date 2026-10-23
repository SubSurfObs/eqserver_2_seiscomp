# Codex dormancy brief — 2026-10-23

Claude's usage credits are nearly exhausted; the project goes dormant for
several days. During that window Codex works with Dan directly. This brief
is Codex's complete instruction set for the period. It supersedes the task
list in `docs/codex_handoff_2026-10-22.md` where they differ.

## What just happened (context you need)

Your full-gate review (`reviews/2026-10-23_v3_full_gate.md`) returned
BLOCK with 11 findings. Claude accepted all 11 and fixed nine the same
day — the Response section of that file maps every finding to its commit.
The fix commits on `workflow-v3` are, in order:

- `12321c3` F1 (selection mandatory; legacy chooser behind explicit flag;
  wrapper preflight + plan gate), `fcc642a` F6 reader half, `fca5ea8` F3,
  `96a265d` F8, `498302b` F10, `7d52e99` F7, `dd12fbb` F4 (schema v0.3),
  `8178fea` F5, `c8e161a` F2.
- Sibling repos: `sudspy@0febe26` (F6 merge half), `disk_to_sds@d277741`
  (F5 write ordering).

All four unit suites pass on the VM at this state
(`scan/test_convert_gates.py`, `scan/test_selection_tails.py`,
`scan/test_preflight_gates.py`, sudspy `tests/test_timestamp_fidelity.py`),
and the proving days (TYHS mixed-format, WLSH multi-channel, LKHRT
drifting clock) audit clean end-to-end. A full 656-day study re-run from
scratch on this exact code is running on the VM; its results land in
`~/v3_test/triage_study/results.jsonl` (each line carries the repo SHAs).

## Your mission for the dormancy window (~48 h of useful work)

**Adversarially verify Claude's fixes.** Claude fixed findings from your
review — the classic failure mode is a fix that satisfies the finding's
letter but not its intent, verified by the person who wrote it. Assume
nothing; re-derive.

### Part 1 — re-review the fix diffs (first, ~half a day)

Re-run your review process over the fix commits listed above (diff range
`26d2006..HEAD` on workflow-v3, plus the two sibling commits). Output:
`reviews/2026-10-24_v3_fix_review.md` in your review format. For each of
the 11 findings: FIXED / PARTIALLY FIXED / NOT FIXED / NEW DEFECT
INTRODUCED, with line-precise evidence. Pay particular attention to:

- F1: is there ANY remaining path to a conversion without a validated
  selection? (Grep every `subprocess` call and every caller of
  `query_cross_source_day_files`.)
- F3: the tail-routing logic in the worker — the leftover-channel
  bookkeeping is fiddly; check the `'*'` tail case and the empty-groups
  day case.
- F5: the winner-first ordering relies on THREE links (SQL ORDER BY →
  group list order → engine read order → merge stable sort). Verify each
  link in code, and that `write_sds` existing-first doesn't break
  disk_to_sds's own SD-card path semantics.
- F6: `DRIFT_SPLIT_MAX_S = 1.0` — construct the adversarial case near the
  boundary (duplicate copy shifted 0.9 s vs drifting clock accumulated to
  0.9 s; both now split — is that acceptable for B2-class duplicates?).
- F7: the fingerprint hashes output files on every skip check — measure
  the cost on a real station-year and say whether the fast path is still
  fast enough to be worth keeping.

### Part 2 — behavioural tests in your sandbox (bulk of the window)

Work on the VM in **your own sandbox only**: `~/codex_sandbox/` (create
it). Copy `~/v3_test/fix_one_day.sh` there and edit YOUR COPY so its
work dir is `~/codex_sandbox/oneday/` — never run the original, so your
runs and Claude's never share a directory (we had a same-directory race
on 2026-10-23; one-writer-per-directory is now the rule).

Suggested tests, highest value first:

1. **Midnight channel-leak (F3), real data.** Find a station-day pair in
   the study sample where day N-1's selection assigns a file Z-only while
   day N's N/E come from telemetry (query the selection SQLite's rules).
   Convert day N in your sandbox; verify with ObsPy that the first
   seconds of each channel come only from the permitted copies.
2. **Probe blind spots (F4).** Pick a station-month with a mid-month
   recorder change (DDNE or FORG 2020-03). Compile a selection; check the
   probes table lists first/middle/last samples and that the channel set
   matches reality on BOTH sides of the transition.
3. **Early-drift boundary (F6).** Rerun LKHRT 2023-07-28 in your sandbox
   and confirm `badsamples=0`; then locate a 1000 Hz day (WLSH 2019 high-
   rate epoch) and audit it exhaustively — the 0.5-sample tolerance is
   0.5 ms there.
4. **Same-size corruption (F7).** Convert a day with `--skip-unchanged`,
   flip one byte in an output day-file keeping size equal, rerun — it
   must reconvert, not skip.
5. **Plan gates (F1).** With a copy of a real plan edited to
   `status: needs_review`, confirm phase3 `--commit` refuses, and that
   `run_production_convert.py` (pointed at your sandbox everything)
   refuses on preflight before touching any data.
6. **Study scoring (F10/F11).** When the full re-run finishes, score
   `results.jsonl`: PASS/FAIL/ERROR counts, failure signatures grouped,
   and verify every line carries the same three SHAs. Write the analysis
   into your handoff, not into Claude's directories.
7. **F9 witness (if time allows).** Design — and if you're confident,
   build on your own branch — the independent day-checker: enumerate SUDS
   block headers and channel labels with your own minimal parser (no
   sudspy, no qa_verify, no suds_convert imports), compare channel
   presence + sample counts against the SDS output. Even a design note
   with the SUDS byte-offsets you verified is valuable.

### VM access and layout

- Host: `rs-l-0ezd3a.desktop.cloud.unimelb.edu.au` = `172.26.144.41`,
  user `dsand` — `ssh dsand@172.26.144.41` works from Dan's Mac via the
  existing ssh key (no password). Passwordless sudo on the VM (you should
  not need it).
- Python: `/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3`
  (has ObsPy, sudspy editable, isal).
- Repos on the VM: `~/projects/SubSurfObs/eqserver_2_seiscomp_v3`
  (workflow-v3), `~/projects/SubSurfObs/disk_to_sds`,
  `~/projects/SubSurfObs/sudspy`, all synced via git only.
- Data: origin archive `/mnt/eqserver_archive` (**read-only, never write,
  never attempt**); shared staging `/mnt/seiscomp_staging` (**do not
  write during this window**); long-term `/mnt/seiscomp_archive` (ro).
  Station DBs: `~/station_dbs/` (DU and some VW) and
  `/mnt/seiscomp_staging/eqserver_provenance/VW/2026-06-10/station_dbs/`
  (VW). Plans: repo `plans/<NET>/`; synth plans
  `~/v3_test/triage_study/synth_plans/` (read-only to you).
- Claude's work areas — READ ONLY for you: `~/v3_test/`, `~/korum_ingest/`,
  `~/perf_corpus/`.

### Contamination rules (operator-ordered, non-negotiable)

1. **Writes on the VM go only under `~/codex_sandbox/`.** Everything
   else on the VM and every mount is read-only to you this window.
2. **No repo working-tree edits on `workflow-v3`, `rewrite-suds2sds` or
   `main`.** If you write code or documents worth keeping, commit them on
   your own branch `codex-break` (branch from current `workflow-v3`
   HEAD), with every commit message prefixed `Codex: `. Claude reviews
   that branch on return; nothing merges before that review.
   Exception: your re-review report goes to `reviews/` — commit it on
   `codex-break` too.
3. **No production actions**: no conversion into shared staging, no
   promotion, nothing on dev1, no registry or plan edits.
4. **Timestamps are converted exactly as recorded on disc — never
   corrected.** Note timing oddities; do not fix them.
5. **End-of-window handoff:** write `~/codex_sandbox/HANDOFF.md` AND
   commit a copy as `docs/codex_break_handoff.md` on `codex-break`:
   what you ran, what passed, what failed with reproduction commands,
   your fix-review verdicts, and anything Dan decided during the window.
   That file is the first thing Claude reads on return.

### What Dan does during the window

Dan runs anything that needs operator authority, decides anything
identity-related (the 7 DU decisions, the engine pin), and adjudicates if
you believe a fix is wrong — with data, in your sandbox, recorded in your
handoff.

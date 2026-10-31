# eqserver_2_seiscomp — agent entry point

Read this file first, then `STATUS.md`. This file is deliberately short and
model-agnostic (OpenCode, Claude Code, Codex, Gemini, etc. all read it).
The long-form design reference is `CLAUDE.md` (~2,300 lines) — **do not
load it whole**; open the section you need via the map below.

## What this project does

Converts the decade-scale EqServer archive (one-minute PC-SUDS `.dmx` and
miniSEED `.ms.zip` files, plus RT130 per-channel files) into a clean
SeisComP SDS archive, network by network (VW, VX, DU), with full provenance
in `sds_staging_ledger`. It replaces the bash + Java EqConvert pipeline in
`legacy/` (reference only — never run it).

## The pipeline (locked vocabulary — new work must fit one stage)

| # | Stage | Host | Current script | Output |
|---|---|---|---|---|
| 0 | REGISTRY | Mac | edit `metadata/station_registry.yaml` | which stations, which network |
| 1 | MANIFEST | staging VM | `scan/level1.py` | `~/station_dbs/<NET>.<STA>.db` (SQLite) |
| 2 | PROFILE | staging VM | `scan/diagnose.py` (wraps 5 sub-scripts) | `metadata/source_stats/**` |
| 3 | PLAN | staging VM | `scan/plan_generator.py`, `scan/regenerate_plans.sh` | `plans/<NET>/<NET>.<STA>.plan.yaml` — **human review gate** |
| 4 | CONVERT | staging VM | `scan/phase3_driver.py` (via `run_production_convert.py`) | staging SDS + run manifest |
| 5 | PROMOTE | **dev1 only** | `sds_staging_ledger/apply.py` (via `run_production_promote.py`) | long-term SDS + ledger events |

## Hosts

| Name | Address | User | Role |
|---|---|---|---|
| Mac | local | DSAND | editing, registry curation, git |
| Staging VM | `rs-l-0ezd3a.desktop.cloud.unimelb.edu.au` / `172.26.144.41` | `dsand` | stages 1–4; origin NFS **ro**, staging CIFS **rw**, LT CIFS **ro** |
| dev1 | `seismology-dev1.its.unimelb.edu.au` | `seiscomp` | stage 5 only; LT mounted **rw** |

Canonical host facts live in the sibling repo `uom_compute/CLAUDE.md`; it wins
on host/mount questions.

## Hard invariants (never break these; stop and ask instead)

1. **Origin archive is read-only.** Never write under `/mnt/eqserver_archive`.
2. **Only `apply.py` on dev1 writes long-term (`/mnt/seiscomp_archive`).**
   Run the dev1 preflight (hostname, `mount | grep seiscomp_archive` shows
   `rw`, touch+rm test file) before any promote. Never delete from LT.
3. **Pin code before production runs.** Every repo used by a stage must be
   at a committed, clean SHA (`git status --porcelain` empty). Stage 4 pins
   `disk_to_sds` + `sudspy` too. The 2026-06 side-load incident came from
   breaking this.
4. **Plans gate conversion.** No conversion without a reviewed plan YAML.
5. **Registry edits:** run `python3 metadata/check_registry.py` before
   committing (YAML silently keeps the last duplicate key).
6. **Convert what is on disc.** Partial days and outages are not skip
   signals; only unreadable files yield no output.
7. **Verify against the real archive before claiming.** Filename/channel
   assumptions must be checked on actual data across years and stations.
   Say what was verified and what was inferred.
8. **No production action without the operator.** Agents may prepare
   commands; Dan runs anything on the VM or dev1 that writes data.

## Roles (Claude builds, Codex reviews, Dan decides)

- **Claude (Claude Code) — implementer.** Writes code, runs tests and
  conversions, updates `STATUS.md` and docs. The only agent that edits files.
- **Codex — independent reviewer.** Read-only. Never edits code, never fixes
  what it finds. Writes review reports to `reviews/` following
  `docs/review_brief.md`. If you are Codex and were asked to review, follow
  that brief and do nothing else.
- **Dan — decides.** Approves plans and production runs; adjudicates
  disagreements.

Review checkpoints (Claude triggers `/codex-review` at each):
1. before a station plan is approved;
2. after any change to conversion or verification code;
3. when interpreting test or trial-conversion results;
4. before any production convert or promote run.

Disagreements are settled with data (convert the day, inspect the archive),
not argument. Record the outcome in the review file.

## Source of truth, in order

1. `metadata/station_registry.yaml` — scope (`include: true|false|unknown`)
   and target network. Only `include: true` stations are converted.
2. FDSN StationXML snapshots in `metadata/uploaded/<NET>/` — channel codes.
3. Per-station plan YAMLs — the per-station conversion contract.
4. Sweep state on the staging share: `python3 scan/sweep_status.py
   --unit-table --queue-dir /mnt/seiscomp_staging/eqserver_sweep`
   (convert_failed.jsonl alone is misleading).
5. Ledger repo `sds_staging_ledger` — what actually reached LT.

## Map: what to read for which task

| Task | Read |
|---|---|
| Where are we / what next | `STATUS.md` |
| Past reviews and their outcomes | `reviews/` |
| Storage tiers, mounts, mediaflux | `CLAUDE.md` § "Shared infrastructure & sibling project", § "Storage architecture" |
| Ledger / promote / provenance | `CLAUDE.md` § "Ledger integration" |
| Station scope & network rules | `CLAUDE.md` § "Station scope and target networks" |
| Recorder types & file naming | `CLAUDE.md` § "Archive characteristics" |
| Channel code remapping & plans | `CLAUDE.md` § "Network and channel code remapping" |
| Manifest / scan levels / schema | `CLAUDE.md` § "Pre-scan and file manifest" |
| Midnight boundary issue | `CLAUDE.md` § "Midnight-boundary data loss" |
| Stage consolidation design | `docs/workflow_consolidation_architecture.md` |
| v2 build order (in progress) | `docs/scan2_build_order.md`, `docs/scan2_planning_progress.md` |
| What v2 resweep must fix | `docs/v1_lt_defects_and_v2_resweep_scope.md` |
| VW recovery state | `docs/scan1_recovery_register.md`, `docs/recovery_register.yaml` |
| DU launch blockers | `docs/du_pre_sweep_notes.md` § "Decisions still owed" |
| Talking to sibling repos | `RELATED_PROJECTS.md`, `handoffs/README.md` |
| Performance numbers | `PERFORMANCE.md` |

`PROGRESS.md` is a historical log (to 2026-05-30); do not treat it as current.

## Communicating with Dan (operator ruling 2026-10-19)

Dan is a geophysicist, not a software engineer. Report in plain English:
describe things by what they are ("the eight test days copied to the VM's
own disk"), never by project-internal coinages ("frozen corpus days" —
the named bad example). Commit hashes, module filenames and review
finding-numbers are jargon; use them only when Dan needs to open the
file. Every status update names which branch/project it concerns. When a
decision is in play, state the options and the recommendation in one
sentence rather than implying it through artifacts. Dan will explicitly
flag replies that communicate well — treat those as the exemplars to
match (recorded in agent memory under feedback-short-plain-english-
default).

Standing rules (Dan, 2026-10-31):
1. **Scope:** this project only ever deals with converting EqServer
   seismic waveform data. Nothing else.
2. **Dictation:** Dan often dictates; read every message as possibly
   garbled speech-to-text and infer the intended words.
3. **Misplaced prompts:** a request that is out of context for this
   project was probably pasted into the wrong window — ask one short
   question and do nothing else until Dan answers.

## Working conventions

- Run scripts from repo root with `python3 scan/<script>.py --help` first;
  most are dry-run by default and need `--commit` to write.
- Tests that need no archive access: `python3 scan/test_parser.py`,
  `python3 scan/test_cross_source.py`.
- Cross-repo requests go in `handoffs/<peer>/<YYYY-MM-DD>_<slug>/NN_<type>_from_<sender>.md`,
  mirrored in the peer repo.
- When a discovery contradicts `CLAUDE.md`, update the relevant section.
  Keep this file short: add pointers here, not detail.

## Session protocol (this replaces chat history)

- **Start:** read `STATUS.md`; run `git log --oneline -15` and `git status`.
- **During:** keep the "Next actions" list in `STATUS.md` honest.
- **End:** update `STATUS.md` (what changed, what was verified, what is
  open, exact next step) and propose a commit message. Anything learned
  that a future model needs must be written into the repo — nothing
  survives in chat.

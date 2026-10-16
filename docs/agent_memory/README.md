# Agent memory notes — HISTORICAL SNAPSHOTS

These 50 notes were recovered on 2026-10-16 from Claude Code's per-project
memory (`~/.claude/projects/*eqserver*/memory/`) so that every model can see
them. Each note reflects what was true **when it was written** (dates in the
file mtimes and bodies, mostly 2026-05/06). They are subordinate to
`STATUS.md` and the current docs: **where a note disagrees with STATUS.md,
`docs/`, or `CLAUDE.md`, the repo wins.**

Known supersessions to watch for (the index `MEMORY.md` still carries the
old claims):

- **"No hand CSV for DU channel codes"** (`du_migration_status.md`,
  2026-05-26) — superseded by `docs/du_pre_sweep_notes.md` § "Sources of
  truth for DU channel codes": the operator-collaborative
  `du_station_registry/du_stations.csv` is now the FINAL word, with
  pipeline resolution order CSV → VIP → FDSN → LT → BLOCKED. The old note
  rejected a *privately hand-maintained* CSV; the public operator-edited
  repo CSV is a different thing.
- **"320 station-years contaminated by the side-load window"**
  (`feedback_side_load_blast_radius_is_full_sweep.md`, index line) —
  the Class-B attribution behind that number was empirically disproven
  2026-06-23 (9/9 candidates checked); see
  `project_class_a_vs_class_b_framing.md` (updated version) and
  `docs/v1_lt_defects_and_v2_resweep_scope.md` for the current ~30–80
  station-year resweep estimate. The *provenance-discipline lesson*
  stands; the blast-radius number does not.
- **`project_next_session_startup_2026-06-23.md` ("START HERE")** — was
  the startup note for the 2026-06-24 session only. Start from
  `STATUS.md`, per `AGENTS.md`.

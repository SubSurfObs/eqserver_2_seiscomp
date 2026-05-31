# Associated projects

Projects that `eqserver_2_seiscomp` collaborates with, directly or via shared
infrastructure. Each peer with a documented cross-project relationship has
a corresponding `handoffs/<peer>/` directory for design conversations — see
`handoffs/README.md` for the convention.

| Project | Role | Repo | Local path (Mac) | Handoff history |
|---|---|---|---|---|
| `disk_to_sds` | Sibling ingest project — pulls SD-card data into the same shared staging SDS skeleton. Writes to the same `events.jsonl` in the ledger as this project. | `github.com/SubSurfObs/disk_to_sds` | `~/projects/SubSurfObs/disk_to_sds` | `handoffs/disk_to_sds/` |
| `sds_staging_ledger` | The system of record for long-term archive promotions. Owns `apply.py` (the single LT writer) and the events/cleanups/cards/policies/runs manifest tree. Both this project and `disk_to_sds` produce data that flows through it. | `github.com/SubSurfObs/sds_staging_ledger` | `~/projects/SubSurfObs/sds_staging_ledger` | `handoffs/sds_staging_ledger/` |
| `sudspy` | Core dependency — PC-SUDS parser used by this project's Phase 3 conversion. Local-only on the user's machine. | (local) | `~/projects/SubSurfObs/sudspy` | no handoff thread (single-author dependency) |
| `uom_seismic_metadata` | Canonical station metadata source (per-network YAMLs). Reference layer for channel codes, sensor models, recorder identity. This project's plans depend on its `station_registry.yaml` snapshot. | (local) | `~/projects/SubSurfObs/uom_seismic_metadata` | no handoff thread yet |

## How a new peer joins the group

1. **Add a row** to the table above describing the relationship — what role
   the new project plays, what data/state it shares with this one, where to
   find its repo and local checkout.
2. **Mirror the addition in the new peer's `RELATED_PROJECTS.md`** so the
   relationship is documented bidirectionally.
3. **Create `handoffs/<new-peer>/` directory** here (just `.gitkeep` if no
   thread is active yet; first message starts a thread).
4. **If the new peer affects this project's architecture** (storage paths,
   shared mounts, contracts, schemas), document those touchpoints in
   `CLAUDE.md` under "Shared infrastructure" or a new dedicated section. Don't
   leave the relationship buried in this file alone.
5. **First handoff thread**: when starting the first conversation with a new
   peer, follow `handoffs/README.md` — create
   `handoffs/<new-peer>/<YYYY-MM-DD>_<topic-slug>/` and write
   `01_<message-type>_from_<sender>.md`. Mirror the file in the peer's repo.

## Why this isn't in CLAUDE.md

`CLAUDE.md` captures stable design intent for this project — the kind of
documentation that a session needs to reason about the code architecture.
Associated-project relationships and handoff conventions are at a different
abstraction layer (project group rather than project internals), and they
change more frequently than CLAUDE.md should. Splitting them out keeps both
files focused.

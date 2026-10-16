---
name: claudemd-cleanup
description: "User wants active reconciliation/clarification of CLAUDE.md during the preliminary stage, not just appends"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 8334aefc-ad49-41d2-9e7a-e1847a54dfc1
---

During the preliminary stage of the EqServer→SeisComP migration, the user wants
Claude to **actively shake out inconsistencies in CLAUDE.md and make it as clear
as possible** — not merely append new notes.

**Why:** CLAUDE.md has been edited by several sibling projects (eqserver_2_seiscomp,
disk_to_sds, sds_staging_ledger) over a long period, so it has accumulated stale,
contradictory specifics (e.g. an old `rd-` VM's staging share name, wrong creds
path, "~25 TB SMB" for what is really a ~16 TB NFS origin). A reader could act on
the wrong stale fact.

**How to apply:** When working in this repo, treat CLAUDE.md as a doc to reconcile,
not just extend. Verify infra/structural claims against the live VM (`rs-l-0ezd3a`,
172.26.144.41) or real archive before trusting them, then correct the doc when
findings differ. Surface a prioritized inventory of remaining inconsistencies and
let the user direct the bigger structural prunes. Reinforces and extends the
existing meta-principle in [[project-overview]] (test assumptions, update the doc).

Inconsistencies flagged 2026-05-25:
- **Channel band-code policy conflict — RESOLVED 2026-05-26.** CLAUDE.md
  "Network and channel code remapping" now holds the full decision tree (FDSN
  inventory first; Gecko convention by sample rate as fallback) + the Gecko
  channel-naming table + the EchoPro orientation caveat; Stage 3 pointer and the
  config.yaml sketch updated; no blanket-`CH` claims remain. See [[du-migration-status]].
- **LT write method — RESOLVED 2026-05-26.** The `rsync` language was obsolete
  (predated the ledger). LT promotion is via `sds_staging_ledger/apply.py`
  (atomic cp, dry-run default, write/skip/override, never deletes). Reframed in
  CLAUDE.md: EqServer→SeisComP is "just another SDS source" like `disk_to_sds`,
  feeding the same ledger; this project's LT-boundary job is to write correct
  ledger records, mirroring `disk_to_sds` (the ledger/apply architecture is only
  a few days old as of 2026-05 but is the chosen mechanism). See [[du-migration-status]].
- **Superseded "Storage architecture" / "VM access" sections — RESOLVED
  2026-05-26.** Trimmed to pointers to "Shared infrastructure"; preserved the
  unique content (three-tier flow diagram, "Implications for pipeline design"
  subsection, the `rd-`/`rs-` NFS-provisioning note, origin-safety rule) and
  dropped duplicated/stale specifics (old mount recipe, stale `user seiscomp`,
  eqconvert.jar paths).

**Status 2026-05-26:** all three originally-flagged inconsistencies resolved.
Doc is now internally consistent on mounts, channel-code policy, and LT-write
method. Remaining work is build, not cleanup (manifest → FDSN fetch → plan
generator → EchoPro driver).

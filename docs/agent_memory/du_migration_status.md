---
name: du-migration-status
description: "DU network migration to new SeisComP server is ~70% done; FDSN inventory is the channel-code source of truth, no hand CSV"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8334aefc-ad49-41d2-9e7a-e1847a54dfc1
---

The DU network's stations are being brought onto the new SeisComP/seedlink server
incrementally. **~70% migrated as of 2026-05-26** (the committed `metadata/uploaded/DU/du.xml`
FDSN snapshot held 29 stations / 39 channels, created 2025-11-04 — ~72% of a
~40-station network). At 100% the conversion can cover all DU stations.

**Why it matters:** channel codes for DU are operator-choice (best-effort SEED
interpretation; Gecko is a *simplified subset* of SEED, e.g. DU uses `SH*`
short-period which Gecko never emits). The decision (2026-05-26) is that the
**FDSN inventory IS the channel map** — fetched by script, committed as a
StationXML snapshot under `metadata/uploaded/<NET>/`, re-run as stations migrate.
**We are NOT maintaining a hand CSV** (user's instinct: a CSV would just drift
from the FDSN truth). This generalises to all networks, not just DU.

**How to apply:** When converting, resolve channel codes via the decision tree in
CLAUDE.md "Network and channel code remapping" (FDSN-first; Gecko-by-sample-rate
fallback for VW/VX). DU stations not yet on the server have no code to inherit.

**DU never-on-server leaf — RESOLVED 2026-05-26** via the per-station
**conversion-plan gate** (CLAUDE.md "Conversion plan (per-station review gate)"):
a DU station-day with no FDSN entry + no metadata is written to the plan as
`UNRESOLVED`/`status: BLOCKED`, and the run refuses until the user confirms or
overrides the code. DU codes follow **full SEED naming**
(https://ds.iris.edu/ds/nodes/dmc/data/formats/seed-channel-naming/) where band =
sample-rate AND corner-period; Gecko is the broadband-only subset (hence DU `SH*`
short-period codes Gecko never emits) — so DU is never auto-guessed from rate.
FDSN URL is known: [[fdsn-server]]. See [[claudemd-cleanup]].

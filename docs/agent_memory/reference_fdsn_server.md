---
name: fdsn-server
description: UoM SeisComP FDSN station web service — source of truth for live station/channel SEED codes
metadata:
  node_type: memory
  type: reference
  originSessionId: 8334aefc-ad49-41d2-9e7a-e1847a54dfc1
---

The project's SeisComP server exposes an FDSN station web service at
**`https://subsurface.science.unimelb.edu.au`** (queries at
`/fdsnws/station/1/query`, e.g. `?net=DU&level=channel&format=text|xml`).
Reachable from the Mac directly (verified 2026-05-26).

This is the **source of truth for channel SEED codes** of any station still live
on the new infrastructure (all networks). The conversion pipeline resolves
channel codes FDSN-first; committed StationXML snapshots under
`metadata/uploaded/<NET>/` are point-in-time pulls and go stale (DU snapshot had
29 stations 2025-11-04; live query returned 37 on 2026-05-26). Re-fetch rather
than trust the snapshot when currency matters. See [[du-migration-status]].

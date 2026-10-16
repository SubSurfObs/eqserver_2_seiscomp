---
name: feedback-closure-is-not-exclusion-rationale
description: Station closure / decommissioning is NEVER a valid reason for include:false in station_registry.yaml. The registry exists to convert HISTORICAL waveform data; whether the station still records is irrelevant.
metadata:
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

`metadata/station_registry.yaml` exists for **converting historical waveform data from the EqServer archive into the long-term SDS archive**. The unit of decision is "do we want this station's past bytes?" — NOT "is the station still recording?"

**A station can be closed, decommissioned, stolen, destroyed, or operationally shut down and still have valuable historical data we want to convert.** None of those are reasons to set `include: false`.

**Valid `include: false` rationales:**
- Archive artifact directory (continuous/, ECHO/, TEST/ — these aren't stations)
- Owned by another agency we don't convert (GA-operated, FDSN-served elsewhere)
- Already converted via a prior pipeline (legacy run, version 1 of this project, etc.)
- Duplicate of another station code

**INVALID `include: false` rationales:**
- "Closed / shut down / decommissioned" — the historical data still matters
- "Station moved to a new code" — convert under the old code through the move date, then under the new code from the move date forward
- "No recent data" — past data is the whole point

**Why:** 2026-06-16 incident. Found 9 stations marked `include: false` with reason `"Operator-confirmed closed/shut down (DL 2026-06-02)"` — GLDS, LILH, ADE, ADE2, GLN, MYP, NBK, TORR, WAL1. All flipped to `include: true target_network: DU`. Operator: "This should never be a reason for include:false, the station registry is all about conversion of historical stations."

**How to apply:**
- If asked to set `include: false` for a station, the rationale must be archive-artifact, ownership-handover, prior-conversion, or duplicate-code. Anything operationally about the station's current state is wrong.
- If reading the registry and seeing a closure-style reason on an excluded station, flag it — it's likely a stale mistake to correct.
- When new stations are added to the registry from operator review, the question is "do we want the historical bytes?" not "is it still recording?"

Linked: [[user-profile]], [[feedback-no-station-heuristics-without-registry]].

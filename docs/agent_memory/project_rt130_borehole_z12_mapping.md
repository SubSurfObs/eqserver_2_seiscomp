---
name: project-rt130-borehole-z12-mapping
description: "RT130 borehole stations (LOYU, WILU, TRPU, MOSU, SGWU) use HH1/HH2/HHZ channel codes because the sensor's down-hole orientation is unknown — 1 and 2 stand in for the unconstrained horizontals. Current operator decision (2026-06-21): keep the existing HH1→HHN, HH2→HHE mapping in the LT archive. A proper remap is a future task that will also need telemetry-side correction."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Confirmed by operator 2026-06-21.

The 5 VW RT130 borehole stations (**LOYU, WILU, TRPU, MOSU, SGWU**) are
all down-hole sensors. The sensor's orientation in the borehole is not
known with sufficient precision, so the horizontal components were
labelled `1` and `2` rather than the conventional `N` and `E`. The
vertical is still `Z`. So the native channel set at acquisition is
`HH1 / HH2 / HHZ`.

**Operator's current decision (keep status quo for now):**

- The operator's running SeisComP server receives RT130 telemetry as
  E/N/Z (i.e. the `HH1` → `HHN` and `HH2` → `HHE` substitutions have
  already been applied somewhere in the ingest chain).
- **VERIFIED 2026-06-21 by direct LT inspection:** the VW LT archive
  PRESERVES the native `HH1 / HH2 / HHZ` naming for RT130-era data
  (2014-2023 sampled across SGWU). LT does **NOT** use HHN/HHE for these
  stations. So the LT-vs-telemetry-server channel-code mismatch exists
  and is real — operator is aware and accepting it.
- The operator considers the E-N substitution "obviously not accurate"
  but is leaving it in place for now because (a) any change to the mapping
  must also rewrite the LT archive bytes that have come through telemetry,
  and (b) it's not blocking the second-pass conversion.

**A future remap (to either revert to `HH1`/`HH2` or apply a properly
calibrated rotation) is deferred.** When it happens it will need to:

1. Decide the canonical mapping (HH1→? HH2→?)
2. Re-write LT bytes for the affected stations + date ranges
3. Update the SeisComP server's metadata so the telemetry side starts
   labelling channels the new way too
4. Document the transition date in metadata

**How to apply (for the second-pass conversion):**

- For RT130 stations, the convert path produces `HH1/HH2/HHZ` from
  source (via the minimus per-channel path — confirmed via SGWU 2018 LT,
  100% coverage on all 3). If a remapping step writes those as
  `HHN/HHE/HHZ` somewhere downstream, keep that step in place. Don't
  invent a new mapping.
- Don't treat the E/N labelling at telemetry-LT as a bug to fix now.
  Operator stance is "stick with E/N/Z until I decide". Surface any
  apparent inconsistency as a note for future work, not a stop signal.

**Stations affected** (per `metadata/station_registry.yaml`):

| Station | recorder_types |
|---|---|
| LOYU | `[reftek_rt130]` |
| WILU | `[reftek_rt130]` |
| MOSU | `[reftek_rt130]` |
| SGWU | `[reftek_rt130, gecko]` (mixed era — RT130 then Gecko) |
| TRPU | `[reftek_rt130, gecko]` (mixed era) |

Linked: [[project-rt130-per-channel-files]],
[[reference-source-stats-tooling]] (the epoch + per-channel discovery
that surfaced this question).

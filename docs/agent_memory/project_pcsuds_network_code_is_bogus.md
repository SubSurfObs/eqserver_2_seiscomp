---
name: project-pcsuds-network-code-is-bogus
description: "PC-SUDS STATIDENT.network field is bogus pre-VW-formal-registration; common values UM/AB/ABC are all parse-noise on the same meaningless field and get mapped to VW by the conversion. Don't treat variation across UM/AB/ABC as a signal."
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

The `network` field inside PC-SUDS STATIDENT (parsed by
`sudspy.parsers.parse_statident`) does NOT carry meaningful network
identity for the VW EqServer archive. Values commonly seen:

- `UM` — unofficial UM-prefix era
- `AB` / `ABC` — AGOS-Bass era (pre-2025-VW-formal-registration)
- empty / `XX` — GIPnet pre-formal-code era
- `VW` — only post-mid-2025

The conversion pipeline (eqserver_2_seiscomp + disk_to_sds) already
maps ALL of these to VW per the station registry. The `network`
field is essentially scratch storage that operators sometimes typed
something into.

**Why:** I (eqserver agent, 2026-06-10) used AB-vs-ABC variation at
OUTU as supporting evidence for an anomaly window finding, framing it
as "the corruption that ate the recorder and gain ALSO ate the
network field." Operator correction: AB/ABC is sudspy parsing the
same bogus 4-char field inconsistently (trailing null stripped vs
not). NOT corruption, NOT a signal. The anomaly window is still
real (recorder_raw_code=`_`, gain=-32767) but the network-field
variation isn't extra evidence.

There's also a sudspy-side TODO: `parse_statident` should normalize
the trailing whitespace / null bytes consistently so AB/ABC don't
both appear. Not load-bearing for VW pipeline since the field is
meaningless anyway.

**How to apply:** When the scanner reports `network_code_seen` as
UM / AB / ABC / empty:

1. Record the raw value in `archive_network_codes_seen` for the
   audit trail (per-station).
2. Do NOT emit `network_code_drift` flags on epochs when codes vary
   across this set. Variation here is parse noise + operator-typed
   scratch, not real drift.
3. The drift that WOULD matter is something like UM/AB/ABC → VW
   appearing in the bytes (the actual 2025 formal-registration
   transition). When the scan extends past mid-2025 data,
   genuine VW codes might appear.

Cross-reference: `feedback-no-station-heuristics-without-registry`
applies in the same shape — don't use raw header fields as signals
without registry/operator context.

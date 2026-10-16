---
name: project-outu-event-history
description: OUTU 2017-04-12 was an EchoPro sample-rate reconfigure (200→250 Hz) + seismometer correction — NOT a recorder swap. The real EchoPro→Gecko swap at OUTU was 2025-04-09 (out of scope for the EqServer scan whose data ends 2025-02).
metadata:
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

OUTU has had two distinct boundary events on its history that look
similar at first glance but are different in kind. Don't conflate them.

**2017-04-12 — reconfigure (NOT a swap):**

- Wiki entry: `VW-OUTU-2017-04-12_reconfigure.md`
- event_type: reconfigure, sub_event: sample_rate_change +
  seismometer_correction
- Same EchoPro + same CMG-6T-1 (sn T6O68) carried across the visit.
- The operator visited, discovered the seismometer had been knocked
  over, corrected it, and reconfigured the sample rate 200 → 250 Hz
  at the same time.
- LT band-code split (`HHZ.D` → `CHZ.D` at 2017 jday 102) is the
  RATE change, not a recorder change.

**2025-04-09 — actual EchoPro → Gecko swap:**

- Wiki entry: `VW-OUTU-2025-04-09_swap.md`
- This is the real recorder swap.
- Out of scope for EqServer scan (archive_last_data: 2025-02-01).

**Why:** eqserver_2_seiscomp CLAUDE.md commit `8f896e0` used the word
"swap" loosely for the 2017-04-12 event when it really meant
"sample-rate transition between band-letter regimes." The empirical
scan's "EchoPro→Gecko transition" framing for 2017-04-12 inherits
that error.

**How to apply:** When describing OUTU 2017-04-12 in commit messages,
PR descriptions, handoff notes, or memory: call it
"reconfigure / sample-rate change" not "swap" or
"recorder change." If the empirical scan ever pins a recorder change
at OUTU 2017-04-12, treat that as a SCAN FINDING WORTH
INVESTIGATING (something is wrong with the data or the picker, since
the recorder didn't change). The 2025-04-09 event is the only OUTU
recorder swap to date.

**v3 picker-fix implication:** when the v3 scanner probes both
.dmx and .ms.zip on mixed-source months, OUTU 2017-04-12 should
surface as a SAMPLE_RATE epoch boundary (200 → 250 Hz) inside a
SAME-RECORDER (echopro) epoch — not as a recorder change. The
fact that 2025-04-09 isn't in EqServer means the only canonical
EchoPro → Gecko transition for OUTU is out of empirical scan scope.

Related: [[project-vw-station-status]] (if/when created with this
sort of per-station event timeline).

---
name: project-disk-to-sds-decide-rule
description: The override decision lives in apply.py:decide() and compares SAMPLES (not size) to a tolerance of full-day expected samples. Already source-kind-agnostic. Plus two known failure modes.
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

`apply.py:decide()` in `sds_staging_ledger` is the source of truth
for write/override/skip decisions. Already wired for eqserver via
`--mode decide` (source-kind-agnostic — no separate path or wrapper).

**The rule** (per disk_to_sds 2026-06-10):

1. Compare `lt_samples` vs `st_samples` (sample counts from
   `sds.count_samples()`), NOT file bytes. Size is too noisy
   (±30-50% from STEIM2 compression variance).
2. Threshold is `--tolerance` (default 0.001 = 0.1%), expressed as
   a fraction of an EXPECTED full day (`rate × 86400`), NOT as a
   fraction of the LT file's own size.
3. Decision tree:
   - LT absent → write
   - `lt_samples >= st_samples` → skip (conservative-toward-LT bias)
   - staged has more, and gain > tolerance × expected → override
   - staged has more, gain trivial → skip
4. To bias more conservatively, raise `--tolerance` (flag value,
   not new code).
5. Cost: header-only (no decode) but reads whole file. ~1 s/file
   over SMB, tens of ms on dev1's local LT. `--fast` skips it
   entirely (size-only fallback).

**Why:** I (eqserver agent, 2026-06-10) was wrong on every axis
about an "80% size rule": wrong quantity (size not samples),
wrong anchor (LT file not a full day), wrong place (claimed it
needed wrapping/wiring — it doesn't). The actual rule has been in
apply.py the whole time. See [[feedback-held-queue-is-load-bearing]]
for the operational consequence (don't auto-commit held entries).

**How to apply:** When asked anything about the override decision
rule, quote the actual rule and the actual flag name. Don't
invent a size-based variant. Don't propose new wrappers. The
existing apply.py is the API.

**Two known failure modes** of decide() that even the right rule
doesn't catch — both need separate mitigations:

1. **Doubled-LT-day trap**: telemetry pipeline artifact, day's
   `lt_samples` ≈ 2 × true count. decide() sees "LT bigger,
   skip" and skips the clean conversion. Tolerance can't fix
   this. Mitigation: duration-ratio QC on LT side (~2.0 ratio
   flag) before letting decide() vote.
2. **Disjoint day-halves**: LT has morning (telemetry), staging
   has afternoon (SD card / EqServer). Both individually
   incomplete; together they'd be complete. decide() picks the
   bigger one and overrides — destroying the half that wasn't
   covered. Fired exactly once in production (WLSH swap day),
   caught only by the held-queue human gate. Tolerance can't
   fix this either. Mitigation: keep the human gate; don't
   auto-promote on override.

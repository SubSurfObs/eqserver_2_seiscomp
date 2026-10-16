# Review brief (for Codex)

You are the independent reviewer for eqserver_2_seiscomp, a pipeline that
converts a decade of EqServer one-minute seismic waveform files (PC-SUDS
`.dmx`, miniSEED `.ms.zip`, RT130 per-channel files) into a SeisComP SDS
archive. Claude wrote the work you are reviewing. You did not.

## Rules for you

- **Read-only.** Do not edit, create, or delete any file except your review
  output. Do not fix what you find.
- Read `AGENTS.md` (hard invariants, source-of-truth order) and `STATUS.md`
  first. Open `CLAUDE.md` only at the section relevant to the review target.
- Judge the evidence (diff, code, raw outputs, data), not any explanation of
  why it is fine. If you were given Claude's summary, form your own view
  from the raw material first.
- Distinguish **verified** (you checked it in code or data) from
  **suspected** (plausible, not checked). Never present a guess as a finding.

## What to check, in priority order

1. **Invariant violations** — any write to the origin archive; any write to
   `/mnt/seiscomp_archive` other than `apply.py` on dev1; bypassing the plan
   gate; production paths that use uncommitted or unpinned code
   (`disk_to_sds`, `sudspy`); registry edits without `check_registry.py`;
   dry-run no longer the default.
2. **Silent data loss** — samples, channels, days or minute-boundaries that
   could be dropped without an error: midnight boundary/SS offset,
   single-channel disk eras, multi-variant telemetry, disk-vs-telemetry
   dedup, RT130 per-channel files, bogus-year filtering, the INT32/STEIM2
   fallback, gaps classified as "source gap" without proof.
3. **Untested assumptions about the formats** — filename grammar, channel
   mapping (e.g. Kelunji c01/c02/c03 → N/E/Z), sample rates, timing,
   recorder detection. Ask: what in the repo or data actually demonstrates
   this? Is the check independent of the code being checked (e.g. does a
   verifier use the same reader as the converter)?
4. **Test validity** — does the test measure what it claims? Sample counts
   from headers vs payload, tolerances that hide loss, cherry-picked days,
   results that pass trivially.
5. **Ordinary bugs** — logic errors, edge cases, error handling, CLI defaults.

## Output format

Write markdown with this structure:

```
# Review: <target>  (<date>, reviewer: Codex)

## Verdict
One line: BLOCK / FIX-THEN-PROCEED / PROCEED.

## Findings
### F1 — <severity: blocker | should-fix | nit> — <short title>
- Where: file:line (or data path)
- Status: verified | suspected
- Scenario: concrete inputs/state → what goes wrong
- Smallest fix or check: ...

## Checked and found OK
Brief list, so silence is not mistaken for approval.

## Response
(left empty for Claude)
```

If you find nothing at a severity level, say so plainly.

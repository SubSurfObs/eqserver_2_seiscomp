# Review: VW/VX staged-versus-LT comparison and promotion protocol  (2026-11-24, reviewer: Codex)

## Verdict
FIX-THEN-PROCEED.

The exact-time/exact-value comparison is the right basis, and the VW/VX conversion can continue. Do not promote from the VW/VX queues yet. The blockers below are confined to the comparison and Stage 5 copy decision; they do not require another conversion campaign.

## Findings

### F1 — blocker — Duplicate timestamps can produce a false `identical` or `superset` verdict
- Where: `scan/compare_lt.py:90-113`; missing regressions in `scan/test_compare_lt.py`
- Status: verified
- Scenario: each LT sample independently asks whether *any* staged sample at that timestamp has the same value. The same staged sample can therefore satisfy multiple duplicate LT samples. Conversely, a staged duplicate counts as an “extra” sample even though it adds no time coverage. Adversarial calls to the comparison logic produced all three unsafe results:
  - LT contains the same `(time, value)` twice and staging once: `superset`, with `staged_extra_samples = -1`;
  - staging contains the same `(time, value)` twice and LT once: `superset`, despite no added coverage;
  - staging contains two different values at one time and one matches LT: `superset`; with equal raw lengths this can be reported as `identical`.
  A replacement based on either of the latter verdicts could introduce internally contradictory data. The first result also shows that the meaning of `superset` is inverted for an LT duplicate.
- Smallest fix or check: canonicalise each file by timestamp before deciding. If one file has more than one **different** value at a timestamp, return `conflict`. Collapse exact repeated `(time, value)` duplicates for coverage comparison. Return `superset` only when the staged set of unique timestamps strictly contains the LT set and every LT timestamp has the same value. If exact duplicates/topology need preserving, report them separately; they must not create a `superset`. Add regressions for LT duplicates, staged identical duplicates, staged differing duplicates, and equal-length differing duplicate multisets.

### F2 — blocker — The comparison ignores waveform identity and sample rate
- Where: `scan/compare_lt.py:47-57`, `scan/compare_lt.py:76-114`
- Status: verified
- Scenario: `_segments` discards each trace's network, station, location, channel and sample rate after converting the rate to a rounded interval. The verdict then compares only calculated times and integer values. A trace carrying the right values under the wrong SEED identity can pass. A one-sample staged trace and LT trace with different sample rates were classified `identical` in an adversarial call because their single time/value pair matched. This is particularly relevant while the re-sweep intentionally retires old channel/location names: the tool must prove that the replacement file contains only the identity named by its SDS path.
- Smallest fix or check: validate every decoded trace against the stream identity encoded by the SDS relative path and record/compare its exact sample-rate epoch. A wrong identity, overlapping different rates, or samples outside that SDS day must return `conflict` (or a distinct hold verdict). Add tests for wrong channel/location/network, different rate with matching first sample, rate changes, and out-of-day samples.

### F3 — blocker — Verdict hashes are not hashes of the bytes actually decoded
- Where: `scan/compare_lt.py:39-56`, `scan/compare_lt.py:76-88`; `docs/handoff_vwvx_promotion.md:25-38`
- Status: verified
- Scenario: each path is hashed, closed, and then independently reopened by ObsPy. If LT changes between those operations—as a live SeedLink day-file can—the verdict can be calculated from different bytes than the recorded hash. The later re-hash normally detects an append, but it does not make the statement “verdict bound to these bytes” true and it does not cover an absent-LT `write` race: an LT file may appear after the `write` verdict.
- Smallest fix or check: read each file once into a stable byte snapshot, hash that snapshot, and decode that same snapshot (for example via `BytesIO`). At action time, recheck the hashes and, for `write`, require that LT is still absent; if LT has appeared, recompute and hold until a new verdict exists. Record the comparator code SHA with the result. Add a mutation/stale-verdict regression.

### F4 — blocker — The handoff is not yet a safe, executable Stage 5 protocol
- Where: `docs/handoff_vwvx_promotion.md:18-42`; `../sds_staging_ledger/apply.py:109-124`, `../sds_staging_ledger/apply.py:280-350`
- Status: verified
- Scenario: the handoff says to act on comparator verdicts, but the only permitted LT writer, `sds_staging_ledger/apply.py` on dev1, still decides from sample counts and has no reviewed path for consuming and enforcing these verdicts. The document also does not require the completed run manifest to match the staged file inventory, and the proposed fixed backup suffix `.pre-eqserver-v3` has no collision/idempotency rule. A manual rename/copy would violate the project invariant that only `apply.py` writes LT; a crash between renaming the old file and installing the new one could also leave the archive missing its active day-file.
- Smallest fix or check: implement verdict consumption in `apply.py` (or its reviewed Stage 5 wrapper), on dev1 only. Before acting, require a completed queue record, readable run manifest, no failed days, and an exact manifest-to-staging inventory. Re-hash/recheck absence under the action path. Make replacement atomic and restart-safe; preserve the old file under a unique hash/run-ID-bound name and never overwrite an existing backup. Append the ledger decision only after the new LT file is installed and verified. Review this small Stage 5 change before the first promotion; no new conversion proof is needed.

### F5 — should-fix — Nanosecond grids are generated by repeatedly adding a rounded interval
- Where: `scan/compare_lt.py:52-56`, `scan/compare_lt.py:67-69`
- Status: verified
- Scenario: `dt = round(1e9 / rate)` is multiplied by the sample index. For a rate that does not divide one billion exactly, rounding error accumulates and a differently split but otherwise equivalent trace can acquire different calculated nanosecond times. The demonstrated VW/VX rates mostly divide one billion, so this is not an immediate reason to stop the re-sweep, but the general comparator claim is broader than the implementation.
- Smallest fix or check: derive each timestamp by rounded rational arithmetic from `t0 + index/rate`, retaining the exact MiniSEED sample-rate representation where possible. Add a non-divisor-rate test across different trace splits.

## Checked and found OK

- Exact time and value comparison closes the original defect in the sample-count-only rule when timestamps are unique, identities/rates are valid, and inputs are stable.
- Off-grid samples are not silently treated as equal; the existing regression exercises this.
- Reading traces without `Stream.merge()` is correct. Merging could retime or weld data. “No merge” should remain, with duplicate/rate/identity ambiguity handled explicitly as above.
- Holding conflicts and unreadable files, rather than attempting an automatic union, is the correct deadline-safe policy.
- For 2025–26 live-feed data, requiring a proven staged superset before replacement is correct after the blockers above are fixed.
- Whole-file replacement loses no LT waveform sample when `superset` has the corrected unique-timestamp meaning. There is no need to merge LT and staging traces in that case.
- The reported KBRI 2025 result (183/183 identical) is a useful real-data smoke test, but an all-identical case does not exercise the duplicate, superset, conflict or live-file action paths.
- The comparison tool itself is read-only and defaults to writing only its JSONL result.

## Response


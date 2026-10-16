---
name: feedback-scans-write-at-intervals
description: "Long-running scans must flush results to disk at intervals (per outer-loop iteration or every N items), not just at end-of-run. Buffered-until-completion makes it impossible to catch bugs or interrupt safely."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

Long-running scan scripts (LT walks, NFS audits, header probes) must write partial results to their `--out` file at intervals — not buffer everything until the for-loop completes.

**Why:**

- Bugs caught early save hours. If a misclassification or off-by-one slips in, a partial output exposes it within the first iteration's worth of work. End-of-run-only output means the bug is invisible until completion.
- Interruptions become safe. If the VM reboots, the SSH session drops, or the user wants to kill the scan, a partially-written output file preserves the work done so far. Buffered output = zero artifact if the process dies.
- User can review interim findings before the full run finishes. For multi-hour scans (LT trace-count took ~12 hr for VW), this matters a lot.

**Real example 2026-06-19:** the `lt_trace_count_scan.py` buffered all per-(sta, year) accumulators until the year-loop completed, then wrote the summary table once at the end. Six hours into the run the output file was still 0 bytes. If the scan had hit a bug (e.g. miscounting Echo files), there'd be no way to know without killing and restarting.

**How to apply:**

For any scan that takes >5 min:

1. Open the `--out` file at top of script (not at end).
2. At end of each natural outer-loop iteration (e.g. per year, per station), write a header + the partial accumulator state to the file, then `fh.flush()`.
3. Also emit per-iteration progress to a separate `--log` (which we already do for beacons).
4. End-of-run can still re-write the final summary — fine to overwrite the last partial with the complete table.

Pattern:

```python
fh = open(args.out, "w")
fh.write("# header...\n")
for year_dir in years:
    ...accumulate...
    # CHECKPOINT — partial output every iteration
    fh.seek(0)
    fh.truncate()
    fh.write("# header...\n")
    fh.write(f"# partial through year {year}\n")
    write_summary_table(fh, accumulators)
    fh.flush()
```

Linked: [[feedback-cold-cache-discipline]] (same family: discipline around long-running observability scripts).

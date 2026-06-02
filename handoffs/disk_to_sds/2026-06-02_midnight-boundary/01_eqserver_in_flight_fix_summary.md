# Midnight-boundary data loss — eqserver landed Option C in flight; B is yours

**From:** Claude session working on `eqserver_2_seiscomp` on 2026-06-02 (AEST).
**To:** The `disk_to_sds` session.
**Re:** A real systematic data-loss bug at day-file midnight boundaries that
also affects disk_to_sds. We patched eqserver mid-sweep with a self-contained
fix (Option C); the more comprehensive fix (Option B, in `write_sds`) belongs
in disk_to_sds since that's where the engine lives.
**Status:** Eqserver fix landed at `eqserver_2_seiscomp@2724c15`, verified
end-to-end. Pinging you with the bug write-up + advice on what Option B
should look like in your project.

---

## The bug, briefly

EqServer (and SD-card) day-directories file minute-files by their
**filename START** timestamp. The `SS` field is the recorder's seconds offset
within the minute — constant within a recording session, changes only on
recorder restart.

Concrete (SS=12):

- `2024-01-15_2359_12_STA.dmx` starts at `23:59:12` and contains 60 s of
  data → extends to **`00:00:12` of day 16**.
- `2024-01-16_0000_12_STA.dmx` starts at `00:00:12` (filed in day 16's
  directory) → extends to `00:01:12`.
- The 12 seconds from **`00:00:00`–`00:00:12` of day 16** live ONLY in
  day 15's `2359_12` file.

When phase3 processes day 15, write_sds writes records for that boundary
trace into both `2024.015` AND `2024.016` SDS files. Then phase3 processes
day 16. write_sds writes a **fresh** SDS file at `2024.016`, **overwriting**
the sliver day-15 left there. The 12 seconds at the start of day 16 are
LOST.

### Verified, with numbers (eqserver staging, 2026-06-02 mid-sweep)

| Station-year | SS pattern | Loss per day per channel |
|---|---|---|
| BRIG 2023 | constant SS=12 | every day starts `00:00:12` → ~12 s lost |
| BRTH 2019 | mixed SS (41-60 s, restarts) | days start `00:00:41-58` → 14-22 s lost |

For BRIG 2023, every CHZ day-file in staging starts at EXACTLY
`00:00:12.000000Z` — perfectly systematic.

### Network-scale impact estimate

Median SS ~ 12 across VW: 12 s × 365 days × 3 channels ≈ **3.65 hours
lost per station-year**. ~1,140 hours across a full VW sweep if
uncorrected. Same magnitude applies to disk_to_sds output proportional
to total SD-card-day-count.

### Why this matters for disk_to_sds too

You wrote the `write_sds` we both share. The same write-day-from-scratch
semantic that loses the sliver in eqserver phase3 will also lose it in
`echopro_usb_to_sds.py` whenever the engine processes a card containing
EchoPro SUDS files with SS > 0. (Gecko on SD card: same — verified BRIG's
SS=12 behavior on gecko stations too. The recorder writes files indexed
by HHMM but data actually starts at HHMM:SS.)

---

## What we did in eqserver (Option C — self-contained, mid-flight)

Commit `eqserver_2_seiscomp@2724c15`. Two new helpers in
`scan/phase3_driver.py`:

1. **`query_boundary_tail_files()`** — pulls day-1's `HHMM='2359'` files
   from the per-station manifest. Year-rollover resolves naturally because
   the manifest is indexed by `(dir_year, dir_month, dir_day)`.
2. **`_trim_to_day()`** — clips the merged stream to `[day, day+1)` BEFORE
   `write_sds`. Uses a 1-microsecond epsilon at endtime so a sample at
   exactly day+1 start (which belongs to day+1) is excluded.

Per-day flow (production path in `_worker_convert_day`):
```
files          = query_cross_source_day_files(...)   # day N's own
boundary_files = query_boundary_tail_files(...)      # day N-1's 2359 (NEW)
stream = read(files + boundary_files)
stream.merge(method=1).split().sort()
stream = _trim_to_day(stream, day)                   # NEW: trim to [N, N+1)
write_sds(stream, staging_root)                      # writes ONLY day N's file
```

Net effect:
- Day-job N captures + writes its own boundary sliver (the first SS seconds).
- Day-job N does NOT write to day N-1's SDS file → no race against day-job
  N-1 under parallel workers.
- The tail of day N's own 2359 file (which spills into day N+1) is dropped
  here but recovered by day-job N+1, which pulls day N's 2359 in as ITS
  boundary tail. Each day-file written by exactly ONE job.

### Verification

Synthetic BRIG SS=12 stream through `_trim_to_day`:
- Pre-trim: trace at `2023-01-14T23:59:12 → 2023-01-15T00:01:11.99` (boundary)
- Post-trim (day=2023-01-15): starts at exactly `2023-01-15T00:00:00.000000Z`
  ✓

Year-rollover query on real BRIG manifest:
- `query_boundary_tail_files(BRIG, 2023-01-01, "echopro", 0.8)` returns
  `…/BRIG/continuous/2022/12/31/2022-12-31_2359_12_BRIG.dmx` ✓

The 29 units already promoted before this commit (BEST + BRIG + BRTH cohort
this morning + everything pre-2026-06-02) still need Option A — a
post-sweep boundary-stitch pass — to recover their loss. The EqServer NFS
source is read-only, so the boundary samples are still on disk and
recoverable any time.

---

## Why we chose C and not B (in eqserver)

B (read existing SDS file + merge + atomic write back) is the right
semantic fix — it would handle the boundary AUTOMATICALLY without
needing the manifest query trick. But in eqserver phase3, **day-jobs run
in parallel** (`workers=4` within a unit). Two adjacent day-jobs could
both try to read-merge-write the same SDS file at the same time. B is
only safe there if the read-merge-write is atomic across processes —
adds file-locking complexity that we didn't want to introduce mid-sweep.

C sidesteps the race entirely by ensuring each SDS day-file is touched
by EXACTLY ONE day-job. No lock needed.

---

## Advice for the comprehensive change in disk_to_sds (Option B)

You're better-positioned for B than we are because:

- **disk_to_sds processes one SD card at a time, day-by-day sequentially.**
  No parallel writes to the same SDS file → no concurrent-write race →
  read-merge-write is correct without file locking.
- B captures BOTH boundaries automatically (day N-1's tail spilling into
  N, AND day N's tail spilling into N+1) — no manifest-query helper
  needed.
- B also handles the **cross-card** boundary case where card 1 ends
  mid-day and card 2 starts mid-day; whichever runs second naturally
  merges with the first's output.
- B benefits eqserver too: if disk_to_sds's `write_sds` becomes
  read-merge-write, eqserver's Option C's trim is no longer strictly
  required (we'd remove the trim and instead rely on the merge to
  resolve overlapping records correctly). C is a belt; B is the
  suspenders that hold things up regardless.

### Proposed shape (pseudocode)

```python
def write_sds(stream, staging_root, atomic=True):
    """Read-merge-write per-channel day-file. Idempotent: re-running
    with the same input yields the same output bytes (samples are
    deduplicated, gaps preserved)."""
    for tr in stream:
        target = _sds_day_path(staging_root, tr)
        # 1. Read existing if present
        if target.exists():
            try:
                existing = read(str(target), format="MSEED")
            except Exception:
                # Corrupt existing file → log + treat as empty so we don't
                # crash. Caller can decide later whether to keep the
                # corrupt file as .corrupt-<timestamp>.
                existing = Stream()
        else:
            existing = Stream()
        # 2. Combine + dedup
        combined = existing + Stream([tr])
        combined.merge(method=1, fill_value=None)  # method=1: discard overlaps
        combined = combined.split()                # break gaps back into traces
        combined.sort(["starttime"])
        # 3. Atomic write
        tmp = target.with_suffix(target.suffix + ".partial")
        combined.write(str(tmp), format="MSEED", encoding="STEIM2", reclen=4096)
        os.replace(tmp, target)                    # atomic on POSIX
```

### Considerations worth thinking through

1. **STEIM2 round-trip fidelity.** When you `read()` an existing
   STEIM2 file and `write()` it back, ObsPy re-encodes. Samples are
   preserved (Steim2 is lossless), but the byte layout of records will
   differ unless they were on identical record boundaries. Acceptable
   if downstream consumers care only about samples, not record-level
   identity. Worth confirming for any external tooling that hashes SDS
   files.

2. **What `method=1` does on overlap.** ObsPy `merge(method=1,
   fill_value=None)` keeps the LATER trace's samples in overlaps.
   That's fine when the new trace and existing trace agree byte-for-byte
   (the normal case for a re-run of the same data). If they DISAGREE
   in overlap (e.g. one was decoded with bit-error tolerance, the other
   without), `method=1` silently picks one. If you need stricter
   semantics, `method=0` ("if traces overlap, raise") tells you about
   the disagreement instead of papering over it. Default to `method=1`
   for ergonomics, expose a knob if anyone needs strict.

3. **Atomicity needs to survive a kill mid-write.** `.partial` →
   `os.replace()` is POSIX-atomic. If killed before the `replace`, the
   existing file is untouched — perfect for `echopro_usb_to_sds.py`'s
   resume-by-default behavior. The killed day re-runs cleanly on next
   pass.

4. **Concurrency from a future caller.** Even if disk_to_sds itself is
   sequential, eqserver phase3 imports `write_sds` and uses it under
   parallel workers. Two options:
   - **(a) Document that `write_sds` is NOT safe under concurrent
     writes to the same target**, and rely on the eqserver-side trim
     (Option C, already landed) to ensure no concurrent writes happen.
     Simplest; what we have today.
   - **(b) Add fcntl.flock around the read-merge-write critical
     section.** Cost is ~zero for sequential callers; correctness for
     all callers. Pattern:
     ```python
     with open(target, "ab") as f:
         fcntl.flock(f, fcntl.LOCK_EX)
         existing = read(...) if target.stat().st_size else Stream()
         combined = ...
         # write to .partial, then os.replace
     ```
     Recommend (b) as belt-and-suspenders. Eqserver C would still work
     correctly under (b) — no change needed on our side.

5. **Empty input.** If `stream` is empty or every trace gets trimmed
   away upstream, current `write_sds` returns `[]` (no-op). Preserve
   this — disk_to_sds resume logic depends on it.

6. **Tests.** A boundary-aware test fixture: write two adjacent days'
   worth of data through `write_sds`, then re-run day N alone with a
   different sample range, and verify day N+1's SDS file is unchanged
   AND day N's contains both the original and the re-run samples.

### Coordination

If disk_to_sds ships B, eqserver can:
- Keep Option C as-is (no harm — `_trim_to_day` becomes a no-op when
  data perfectly aligns with day boundaries, and a safe trim when it
  doesn't).
- OR remove the trim and rely on B's merge. We'd need to test that
  parallel-worker writes resolve cleanly under fcntl.flock first.

Either way, the **post-sweep boundary-stitch pass (Option A)** is
independently useful for recovering loss from data already promoted
before C/B landed. Same engine — once B is in `disk_to_sds@write_sds`,
A becomes "re-process every promoted day through phase3 with B-enabled
write_sds, idempotent merge handles the rest."

---

## Cross-references

- eqserver code: `scan/phase3_driver.py@2724c15`
  (`_trim_to_day`, `query_boundary_tail_files`, `_worker_convert_day`)
- eqserver doc: `CLAUDE.md` "Midnight-boundary data loss (KNOWN ISSUE,
  discovered 2026-06-02)" section (commit `0089b28`, updated `56fbc5b`)
- engine pin: `disk_to_sds@9a3b2ae` (still current — B should ship as a
  new SHA we then re-pin on eqserver side)
- prior thread: `handoffs/disk_to_sds/2026-06-01_engine-provenance/`
  (the audit trail that ultimately led to confidence in the engine pin
  + this conversation; B is the next chapter of that story)

Ball in your court for B. No rush — eqserver C closes the bleeding for
the in-flight sweep + everything after. B is the right long-term home
and worth doing carefully.

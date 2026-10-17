# Review: Stage-4 conversion hot-path efficiency  (2026-10-17, reviewer: Codex)

## Verdict

PROCEED — no efficiency blocker; F1 has the highest expected fresh-run yield, while F3 is the largest conditional win for retries and reruns.

## Findings

### F1 — should-fix — Rank 1: Minimus is limited by thousands of serialized NFS opens, not ObsPy decode

- Where: [scan/phase3_driver.py:189](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:189), [scan/phase3_driver.py:472](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:472), [scan/phase3_driver.py:490](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:490), [PERFORMANCE.md:341](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/PERFORMANCE.md:341)
- Status: verified
- Scenario: a Minimus day supplies about 4,320 ZIP files. `_concat_zip_members` performs `open → read → close → ZipFile → extract` serially for every file. Only after all members have been collected does production make one bulk `obspy.read()` call. Therefore ordinary Minimus conversion already amortises ObsPy startup; the remaining fixed cost is approximately 4,320 pathname lookups, opens, reads, closes, ZIP central-directory parses, and member extractions per day. DDWB’s measured 7.0 s local versus 75.0 s cold-NFS time, together with its flat w=4/8/16 curve, identifies small-file NFS latency/IOPS saturation as the explanation for Exp 3.
- Effect size: the measured CPU/local floor is about 7 s against 75 s cold, so the theoretical read-phase ceiling is roughly 10×. A realistic bounded-prefetch improvement is more likely 1.5–3×, and could be near zero if the mount/server is already saturated at four outstanding operations. An inner thread pool added on top of w=4–8 is unlikely to help unless total NFS queue depth is held constant.
- Smallest fix or check: benchmark a bounded station-level producer that fetches the next few day directories to local scratch with 1/4/8/16 transfer streams while 4 conversion workers consume local files. Do not multiply inner threads by the existing outer worker count. Use DDWB 2021-06-15 from `~/perf_corpus` as the 7 s CPU control, then a matched cold-NFS day for the I/O result. Record source `openat/read/close` counts and NFS RPCs; require sample-equivalent output and unchanged read-error reporting.

### F2 — should-fix — Rank 2: EchoPro pays thousands of Python/ObsPy object constructions per day

- Where: [suds_convert.py:96](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:96), [blocks.py:94](/Users/DSAND/projects/SubSurfObs/sudspy/sudspy/blocks.py:94), [blocks.py:98](/Users/DSAND/projects/SubSurfObs/sudspy/sudspy/blocks.py:98), [obspy.py:127](/Users/DSAND/projects/SubSurfObs/sudspy/sudspy/obspy.py:127), [obspy.py:137](/Users/DSAND/projects/SubSurfObs/sudspy/sudspy/obspy.py:137)
- Status: verified
- Scenario: every SUDS file independently creates a gzip reader, parses its gzip header, performs several size-specific `read()` calls, constructs a temporary `Stream`, and normally constructs three `Trace`/`Stats`/`UTCDateTime` object graphs. `convert_suds_files` then remaps and appends those traces one at a time. A 1,440-file day therefore creates roughly 1,440 temporary Streams and 4,320 Trace/Stats/time objects before the traces are consolidated.
- Effect size: unmeasured in the production path. The corpus reports 18–22 s local read/decode for representative Echo/EchoPro days, which leaves meaningful CPU headroom, although that benchmark adds auditor-specific work noted in F7. A one-shot compressed-file reader plus day-batch SUDS assembler plausibly saves 10–30% of local read/decode time and roughly 2–10% of a cold-NFS day. It will not remove the mandatory gzip header per independently compressed file.
- Smallest fix or check: first A/B the least invasive variant—read each compressed file once into bytes and use one-shot `igzip.decompress` plus an in-memory block iterator. Only then test a day-batch parser that accumulates native arrays and primitive timing metadata before constructing final traces. Profile the real Stage-4 path on BEST, HOLS, MOE3 and WLSH corpus days. Compare wall time, Python call counts, peak RSS, read-error diagnostics, and final sample arrays.

### F3 — should-fix — Rank 3: an identical rerun rereads every source and then rereads existing SDS

- Where: [scan/phase3_driver.py:833](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:833), [scan/phase3_driver.py:658](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:658), [suds_convert.py:199](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:199)
- Status: verified
- Scenario: every date in a supported epoch becomes a job regardless of an already completed identical output. A retry or repeated invocation reopens and decodes all selected source files. `write_sds` then notices the existing SDS day-file, reads it, combines it with the newly decoded copy, deduplicates, and rewrites it.
- Effect size: approximately 90–99% of wall time is avoidable on a fully completed identical rerun, but the benefit is zero on a first clean sweep. Partial retries benefit in proportion to the already completed fraction.
- Smallest fix or check: define a day fingerprint from the reviewed policy/selection identity plus manifest path, size and mtime fields, and store it with the run state. Run all eight corpus days twice into the same sandbox. The current second run establishes the penalty; a candidate fast path should make unchanged days DB-only, retain byte- or sample-identical SDS, and invalidate when any selected input or policy changes.

### F4 — should-fix — Rank 4: one bad MiniSEED record causes a second full NFS pass and thousands of `obspy.read()` calls

- Where: [scan/phase3_driver.py:384](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:384), [scan/phase3_driver.py:400](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:400), [scan/phase3_driver.py:490](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:490), [scan/phase3_driver.py:506](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/phase3_driver.py:506)
- Status: verified
- Scenario: the normal bulk parse first reads and extracts every ZIP member. If one record makes the bulk ObsPy parse fail, both Gecko and Minimus fallback loops reopen every source ZIP from NFS and invoke `obspy.read()` separately for every member. Good files therefore pay two NFS reads, and Minimus can pay about 4,320 ObsPy parser initializations.
- Effect size: roughly one additional full read phase—about 40–170 s on a cold affected day from the recorded cohort timings—plus per-member parser CPU. At the comment’s approximate 1-in-100 Gecko incidence, catalogue-average gain is probably under 1–3%, but the affected-day latency reduction can approach 2×.
- Smallest fix or check: retain extracted member bytes or offsets until the bulk parse succeeds, and isolate corruption by parsing batches recursively rather than immediately dropping to one call per file. Inject one damaged member into local copies of STBK clean and DDWB corpus days. Confirm one source open per file, identical good-record output, the same corrupt-file QC attribution, and measure both local CPU savings and the separate cold-NFS reread saving.

### F5 — should-fix — Rank 5: int16 SUDS is copied and widened before consolidation

- Where: [obspy.py:135](/Users/DSAND/projects/SubSurfObs/sudspy/sudspy/obspy.py:135), [suds_convert.py:177](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:177), [suds_convert.py:209](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:209), [utils.py:278](/Users/DSAND/projects/SubSurfObs/sudspy/sudspy/utils.py:278), [suds_convert.py:221](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:221)
- Status: verified
- Scenario: SUDS parsing copies each payload into a writable NumPy array. For STEIM output, `write_sds` then casts every small trace to int32 before `fast_merge_split`, so the day-sized concatenation copies widened data. `_to_int32` is called again after merging, although that second pass is normally a no-op. On native int16 days, merging native data first and converting the few merged output traces would halve merge-copy bandwidth and replace thousands of small casts/allocations with about three large casts. A read-only `frombuffer` view may also remove the initial copy, but that needs an explicit ObsPy/libmseed compatibility test.
- Effect size: likely 5–15% of local writer/merge CPU and around 1–5% of an all-local Stage-4 day; under 1% on cold NFS. The more important gain may be roughly 0.1–0.3 GB lower transient memory per high-rate day per worker. Native-int32 MiniSEED gains almost nothing.
- Smallest fix or check: on local HOLS and BEST corpus days, separately benchmark (1) moving the int32 cast after consolidation, (2) removing the second `_to_int32` pass, and (3) retaining read-only payload views until consolidation. Capture peak RSS and allocation counts as well as wall time. Require exact sample equality, dtype `int32` at STEIM2 encode, and equivalent fallback behavior for native float data.

### F6 — nit — Rank 6: the writer performs avoidable staging-filesystem metadata round-trips

- Where: [suds_convert.py:199](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:199), [suds_convert.py:219](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:219), [suds_convert.py:242](/Users/DSAND/projects/SubSurfObs/disk_to_sds/scripts/suds_convert.py:242)
- Status: verified
- Scenario: each output channel performs `exists()`, then a separate `stat()` when it exists, repeats `mkdir(..., exist_ok=True)`, and performs another `stat()` after replacement solely to report size. These target CIFS rather than origin NFS, but still add metadata round-trips to every channel-day.
- Effect size: probably tens to low hundreds of milliseconds per day: perhaps 5–20% of the measured 0.6–0.8 s write phase, but generally below 1% of cold end-to-end conversion.
- Smallest fix or check: count staging filesystem calls with `strace -c` for all eight corpus days on local output and then the CIFS sandbox. Compare a single-stat/cached-parent/tmp-size variant. Final SDS bytes and reported sizes must remain identical.

### F7 — should-fix — Enabler: the existing corpus benchmark does not execute the production hot path

- Where: [scan/perf_read_bench.py:55](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/perf_read_bench.py:55), [scan/qa_verify.py:258](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:258), [scan/qa_verify.py:261](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:261), [scan/qa_verify.py:278](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/qa_verify.py:278), [scan/profile_single_day.py:162](/Users/DSAND/projects/SubSurfObs/eqserver_2_seiscomp/scan/profile_single_day.py:162)
- Status: verified
- Scenario: `perf_read_bench.py` uses the auditor reader. That reader converts SUDS samples to int64 and calls ObsPy separately for every MiniSEED ZIP member, whereas production concatenates members and calls ObsPy once per day. It also performs `is_file()` and `stat()` for each source, operations avoided by Stage 4’s manifest-driven source list. Separately, `profile_single_day.py` still times the obsolete ObsPy `merge()+split()` path rather than current `fast_merge_split`; its 16.2 s SGWU merge anomaly is therefore not a current-production diagnosis.
- Effect size: no direct runtime gain, but local CPU effect estimates for F1–F6 can be wrong by multiples if this harness is used. The cold-NFS conclusion remains directionally credible because network time dominates.
- Smallest fix or check: make the eight-day benchmark invoke the real production converters and `write_sds` against a sandbox, with phase timers around selection, source fetch, decode, consolidation and write. Keep the auditor as the independent output checker, not the timed implementation.

## Checked and found OK

- Stage 1 performs and persists source size/mtime stats; Stage 4 selects using manifest `size_bytes` and does not explicitly restat every source. PLAN does not reread waveform payloads.
- The ordinary Gecko/Minimus path already makes one bulk ObsPy read per day. Per-tiny-member ObsPy overhead exists only in the corruption fallback.
- `cross_source.select_files_for_day` is not a worthwhile target: a local synthetic check took about 0.43 ms for 1,440 single candidates and 3.04 ms for 4,320 Minimus rows. The station/date composite SQLite index is present.
- Boundary handling rereads only the preceding day’s final minute: one file for full-station sources or about three for Minimus. That is roughly 0.07% additional opens/bytes and is not worth optimizing independently.
- `fast_merge_split`, python-isal, whole-ZIP reads, and the measured w=4–8 worker knee are already implemented or measured and were not re-proposed.
- The second `fast_merge_split` in `write_sds` is usually operating on only a few already-consolidated phase3 traces, so removing that call alone is unlikely to produce a material wall-clock gain.

## Response

Responder: Claude (Claude Code), 2026-10-17. All findings ACCEPTED as the
Track-2 experiment queue, in this order (one at a time, per the
one-in-flight rule). No code changes until each experiment's harness run
justifies it.

- **F7 — ACCEPTED, FIRST.** Correct: `perf_read_bench.py` times the
  auditor's readers, not production. Exp 11's cold-NFS conclusion stands
  directionally (network dominates regardless of reader), but before any
  CPU-side decision the benchmark gets rebuilt to invoke the real Stage-4
  converters + `write_sds` into a sandbox with phase timers; the auditor
  stays as the output checker only.
- **F1 — ACCEPTED, the headline experiment.** Bounded producer staging
  next day-dirs to local scratch with 1/4/8/16 transfer streams feeding 4
  converting workers, constant total NFS queue depth, DDWB as the control
  pair. This is the operator's bulk-staging idea made testable.
- **F3 — ACCEPTED.** Day-fingerprint fast path (policy/selection identity
  + manifest size/mtime) so identical reruns are DB-only. Measured by
  running the 8 corpus days twice. High value for Track-3 repairs and
  recovery reruns; zero for first sweeps — priced accordingly.
- **F2 — ACCEPTED.** One-shot decompress + in-memory block iterator A/B
  first; day-batch SUDS assembly only if that pays. Honest expectation
  10–30% of local decode, small on cold NFS.
- **F4 — ACCEPTED.** Keep extracted member bytes until bulk parse
  succeeds; recursive batch isolation instead of per-file fallback.
  Injected-corruption test on local STBK/DDWB copies.
- **F5 — ACCEPTED.** Cast-after-consolidation + drop the redundant second
  cast; RSS and exact-sample checks required. Modest CPU, real memory win.
- **F6 — ACCEPTED (low).** strace-counted writer metadata round-trips;
  bundle with F5's writer work if it happens.

The "Checked and found OK" list is noted with thanks — it retires the
selection-cost and boundary-reread hypotheses and confirms the bulk-read
path was already right.
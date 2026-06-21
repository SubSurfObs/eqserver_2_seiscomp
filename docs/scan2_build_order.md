# Scan 2 — integrated build order

Written 2026-06-21 immediately before a context compaction. This is the
**canonical sequence** for landing the second-pass conversion pipeline.

Read this together with `docs/scan2_planning_progress.md`, which logs
the discovery work that justifies these steps.

## Why this order

The discovery work surfaced patterns the existing pipeline doesn't
handle: multi-variant telemetry, single-channel-disk eras at 26 stations,
RT130 per-channel files via the minimus path, recorder configuration
transitions over time. We built a bag of disconnected scripts that
produce JSON files but DON'T feed into `plan_generator.py` or
`phase3_driver.py`. Pressing forward with the test suite right now would
just re-prove what production already does.

The 6 steps below integrate the new information into a single decision
chain culminating in the test suite running against the integrated
workflow.

## The 6 steps (must be done in order)

### Step 1 — Conditional bisection in `channel_epoch_scan.py`

Currently yearly granularity only. Tightens to monthly (then weekly)
WHERE TRANSITIONS WERE DETECTED, not everywhere.

- For each `(sta, source_kind)` epoch boundary detected at yearly
  resolution, sample mid-month files within the year of change
- Binary-search inward until boundary is pinned to ≤ 30 days
- Output schema gets `boundary_resolution_days: int` per epoch
- Most stations stay cheap; only transition stations pay the cost
- Self-contained — doesn't depend on any other step

### Step 2 — Size-distribution signal added to the data

File size is a useful **Bayesian prior** even though it shouldn't be the
only signal. The original cross_source size-ratio rule was directionally
right but applied as if it were definitive.

- Compute per `(sta, source_kind, epoch)` size statistics from manifest:
  `{p10, p25, p50, p75, p90}` plus a `n_samples` field
- Store alongside epochs: `metadata/source_stats/_epochs/<STA>.json`
  gains a `size_stats` block per epoch
- Prior: "clean 3-channel days have median file size X — anything
  materially smaller is suspicious"
- Posterior: when epoch directly says "disk has only CHZ", that
  dominates the size signal. When epoch is unknown, size breaks ties.
- Self-contained — manifest-only, no NFS reads

### Step 3 — Plan generator integration

`plan_generator.py` becomes the load-bearing decision producer.
The plan YAML is the **single decision authority** for the converter
— human-reviewable, versioned in repo, consulted by phase3.

Plan YAML gains per-epoch entries:
```yaml
epochs:
  - id: 2
    start: 2019-01-15   # bisected boundary
    end:   2022-09-30
    recorder: gecko
    source_priority:
      CHZ: [disk_mseed, tele_ss_mseed, tele_noss_mseed]
      CHN: [tele_ss_mseed, tele_noss_mseed]   # disk doesn't have CHN this era
      CHE: [tele_ss_mseed, tele_noss_mseed]
    size_prior:
      disk_mseed:        {p10: 12000, p50: 16000, p90: 41000}
      tele_ss_mseed:     {p10: 35000, p50: 38000, p90: 47000}
    multi_variant_tele: true
    multi_variant_resolution: tele_ss_mseed   # pick this variant consistently
```

Implementation:
- `plan_generator.py` reads `_epochs/<STA>.json` and the size-stats block
- For each station epoch, emits the `source_priority`, `size_prior`, and
  multi-variant resolution decisions
- The classifier work (`check_manifest.py`) feeds the existing buckets;
  not changed in this step

### Step 4 — `coverage_planner.py` consumes the plan

NEW module that replaces the in-line decisions currently in
`cross_source.select_files_for_day`. Per-channel-per-minute selection.

```python
def plan_day(plan_yaml, manifest_rows, sta, date) -> List[selection]:
    """For each (channel, HHMM) cell in the day, decide which source file
    to use. Output: list of (source_path, channels_to_use, time_window).
    Same physical file can be selected for multiple channels if it carries them.
    """
```

Algorithm:
1. Look up the epoch for (sta, date) from the plan
2. For each (channel, HHMM):
   - Get `sources_that_have_this_channel` from `source_priority[channel]`
   - Filter manifest rows to that minute matching those source kinds
   - Apply size prior as tiebreaker within the priority order
   - Pick the file; record the selection
3. Return the list

`cross_source.select_files_for_day` stays as **legacy fallback** for
days outside any plan epoch — never the primary path once Step 3 lands.

### Step 5 — `phase3_driver.py` integration

- `_worker_convert_day` calls `coverage_planner.plan_day(plan, manifest, sta, date)`
  instead of `query_cross_source_day_files`
- `convert_*_day` engines accept channel-restricted file lists (each file
  has a `channels_to_use` field) — extract only assigned channels from
  each file before merging into the Stream
- The Stream merge becomes per-channel: 3 independent stages, one trace
  per channel out
- Engine output rejoins for SDS write

### Step 6 — ONLY NOW the test suite

The 22-day test source archive from
`docs/scan2_planning_progress.md` becomes meaningful — each day
exercises the integrated workflow, and the verifier output tells us
whether the new pipeline improves on what's in LT.

- Test source archive set: 22 days covering every category
- Run convert via `test_env_run.py convert <sta> <date>`
- Verify via `test_env_verify.py` — get per-day verdicts
- Aggregate verdicts across the batch: go/no-go for the scan 2 launch

## Forbidden until Step 5 is real

- Adding speculation-only days to the test source archive (already
  covered the bug-known ones; more would be theatre)
- Running diagnostic scripts that don't directly feed the planner
- Re-running the network discovery (it's done; outputs in repo)

## State as of compaction

### Completed (all in repo)
- `scan/categorize_source.py` + outputs `metadata/source_stats/<STA>.{json,txt}`
- `scan/discovery_audit.py` + outputs `_discovery/<STA>.{json,txt}`
- `scan/channel_epoch_scan.py` + outputs `_epochs/<STA>.{json,txt}` (yearly granularity)
- `scan/investigate_unmatched.py` + outputs `_unmatched/<STA>.json` + `_network_summary.{json,txt}`
- `scan/probe_unknowns.py` + outputs `_probes/<STA>_probe_a.json` + `_probes/<STA>_probe_b.json` + `_network_summary.{json,txt}`
- `scan/test_env_build.py` + `test_env_run.py` + `test_env_verify.py` — wraps level1/plan_generator/phase3
- 5 test-env days seeded: STBK 2022-10-23, STBK 2023-01-08, HOLS 2018-06-15, HOLS 2018-01-04, BEST 2025-03-30

### Currently running (background on staging VM)
- `count_underscore_tele.py` — counts underscore-tele files per VW station to answer "is the underscore-tele pattern RT130-specific or wider". Output: `/private/tmp/claude-501/.../tasks/btkdinwzu.output` and on-VM via the bash watcher. Will tell us which extra stations need filename-grammar extension.

### Findings (in repo as memory notes too)
- 16 of 41 VW stations have multi-variant telemetry pattern
- 32 single-channel-disk eras across 26 stations
- 34 of 41 stations have unmatched-grammar filenames (~4M total)
- 1.65M underscore-tele files concentrated 97% at RT130 stations (SGWU, TRPU, LOYU); tagged `recorder_type='gecko'` `exclude_reason='unparsed'` by level1; ignored by phase3
- RT130 LT data verified at SGWU 2018: 100% coverage all 3 channels at 200 Hz under HH1/HH2/HHZ naming
- SGWU LT channel naming consistent HH1/HH2/HHZ through 2023; CH?/DH?/FH? in 2025-2026 Gecko era
- **SGWU LT coverage shockingly low in some years**: 2014 (23% of year), 2021 (2.5%!), 2022 (37%), 2023 (43%) — may correlate with the unparsed-cohort drop

### Open immediate questions
- Are the underscore-tele files at SGWU pre-2025 unique data, or duplicates of the per-channel `_001.mseed` files? Probe A returned 4 sample minutes confirming the files ARE real waveform data with HHZ/HH1/HH2 at 200 Hz. Quantitative comparison vs per-channel files still pending.
- Why are SGWU LT counts so low for 2014, 2019, 2021, 2022, 2023? Source-vs-LT audit per year would answer.
- For each station with multi-variant telemetry detected by discovery_audit, confirm the multi-variant variant choice (more files vs better coverage) on a test day.

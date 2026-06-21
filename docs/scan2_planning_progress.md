# Scan 2 planning — progress log

Tracking the work building toward a second-attempt VW conversion that handles
the patterns scan 1 missed (multi-variant telemetry, single-channel disk eras,
unknown-grammar filenames). Sister document to `scan1_recovery_register.md`.

## Motivation

Scan 1 was launched without exhaustive characterisation of the EqServer
source-data shape. Cracks surfaced during conversion (Class B fragmentation,
HOLS test-run-clip, ~136 LT day-files with abnormal trace counts). When we
finally dug in we found at least one major pattern (two-variant telemetry per
HHMM slot) that had been visible in the manifest all along — we just hadn't
asked the right query. The current effort is **"do the discovery work that
should have preceded scan 1"** before designing the planner that drives a
second scan.

## Catalogue of new tooling

All scripts under `scan/`; outputs persist under `metadata/source_stats/`.

| Script | Purpose | Outputs |
|---|---|---|
| `categorize_source.py` | Per-station day-shape bucketing (`A_pure`, `D_pure`, `E_multivar`, `AB_mixed`, `C_partial`, `weird`, `empty`, `sparse`) + full per-day classification list + per-bucket representative day. | `metadata/source_stats/<STA>.{json,txt}` |
| `discovery_audit.py` | "Unknown-unknown" surveillance. Enumerates every distinct combination of manifest column values, flags *ambiguous signature pairs* (sigs that share all but one column — the multi-tele-stream pattern), records filename-grammar coverage with samples of unmatched, flags bimodal size distributions. | `metadata/source_stats/_discovery/<STA>.{json,txt}` |
| `channel_epoch_scan.py` | Yearly-sampled header reads per `(sta, source_kind)` to detect when the recorder's channel set changed. Outputs epoch timelines `start..end → [CHZ,CHN,CHE]`. | `metadata/source_stats/_epochs/<STA>.{json,txt}` |
| `investigate_unmatched.py` | Tokenises unmatched filenames into shape templates (`D{n}`, `H{n}`, etc.). Step 1 pools the 30-sample-per-station lists. Step 2 deep-dives stations with >1000 unmatched. | `metadata/source_stats/_unmatched/{<STA>.json,_network_summary.{json,txt}}` |
| `test_env_build.py` | Per (sta, date): `rsync` source from EqServer NFS into a segregated mirror, run `level1.py` against the mirror (regenerates the manifest with current code), invoke `plan_generator.py` against that manifest (regenerates plan), build a `time_index` DB recording every source file's per-trace timestamps. Idempotent `add` + `rebuild` subcommands. | `/home/.../test_env_classb/` + `catalogue.yaml` |
| `test_env_run.py` | Pre-flight (refuse if unknown classifications), wipe staging, invoke `phase3_driver.py` against the test env, summarise from the emitted run-manifest. | `staging_sds/` + per-run log + run-manifest |
| `test_env_verify.py` | Opens the SDS day-file, finds gaps, back-traces each gap to the `time_index` to classify as **`pipeline_loss`** (source had it, SDS dropped it) or **`source_gap`** (genuinely no source). | per-day verdict (PASS/FAIL with breakdown) |

## Network-wide findings (so far)

### From `categorize_source.py` (STBK / OUTU / CLIF + targeted others)

STBK: 99.6% full from EITHER kind. ~16% of days carry the multi-variant tele
pattern. AB_mixed (disk + single-tele) dominates at 59%.

OUTU: 89.7% full from either; mostly disk; **no multi-variant**.

CLIF: only 62.2% covered by a single source — **38% genuinely need
per-minute disk+tele stitching**. The hardest EchoPro shape we've seen.

### From `discovery_audit.py` (all 41 VW stations)

- **16 of 41 stations have the multi-tele-stream pattern.** Not just STBK.
  ~24 M rows network-wide. Top: DDSW (2.9M), DDNE (2.9M), STBK (2.9M),
  WDSD (2.8M), DDWK (2.1M), WLSH (2.0M), BRTH (2.0M).
- **34 of 41 stations have unmatched filenames** (filenames not matching
  any documented regex). ~4 M files network-wide. Top: SGWU (1.4M),
  TRPU (940k), MARD (428k), LOYU (427k), NARR (185k).
- **41 bimodal-sized signature groups flagged** — effectively every
  station has at least one signature where the size distribution
  suggests two populations we haven't yet distinguished.

Detailed investigation of the unmatched filenames completed 2026-06-21
(see `metadata/source_stats/_unmatched/_network_summary.txt`). The
breakdown, with operator-confirmed context where available:

- **1.7M files with `'YYYY-MM-DD HHMM_STA.ms.zip'`** — mixed space/underscore
  separator, violates the project's documented "space=tele, underscore=disk"
  discriminator. Predominantly Minimus stations (DDWB, DDBE, SCM2).
  **IN SCOPE for scan 2** — must be characterised. Note in
  `project-underscore-tele-files` memory.
- **970k `.wrno.dmx.gz` files** — GPS Week Number RollOver-corrected
  cohort. Filename dates are uncorrected bogus dates (e.g. 1989-08-17);
  real date = filename + 19.7 yr × N. Tagged by the EqServer ingest path
  during the WNRO timing fix. May carry real seismic data; do not
  auto-exclude on filename date. See `project-wrno-files` memory.
- **1.2M per-channel mseed files** at RT130 borehole stations (LOYU,
  WILU, TRPU, MOSU, SGWU). RefTek RT130 wrote single-channel files via
  pre-ingest conversion. Currently invisible to the converter — **scan
  1 silently dropped most of these stations' data**. Must aggregate
  per-channel into 3-component output. See
  `project-rt130-per-channel-files` memory.
- **27k `.trig.ms.zip` files** — triggered Gecko mseed (we handle
  `.trig.dmx` for EchoPro; not the Gecko equivalent). Cheap regex
  addition.
- **11k `XXhr STA.ms.zip` files** — hourly aggregates for some Gecko
  stations. Cheap regex addition.
- **Pre-2012 anything (1989, 1999)** — operator confirmed out of scope
  for VW.
- **~50 FAT 8.3 truncated names** — rare; ignorable for VW second pass.

### From `channel_epoch_scan.py` (all 41 VW stations)

- **32 single-channel disk eras across 26 stations.** The "disk recorder
  configured to vertical only for years at a time" pattern is the NORM
  not an exception. Examples:
  - BRTH `disk_mseed` 2018-2023 was CHZ-only
  - STBK `disk_mseed` 2019-2022 was CHZ-only
  - MARD `disk_suds` 2019-2025 was c03-only (covers most of EchoPro era!)
- **15 stations had 3-4 distinct disk recorder configurations over time.**
- Implication for the planner: for most stations, fetching CHN/CHE for
  large date ranges *requires* telemetry — disk simply doesn't carry it.
  Without this lookup the planner silently drops horizontal channels.

## Planner design — current state of the discussion

Earlier proposal: **per-channel-per-day** selection (pick one source per
channel for the whole day). Walked back to **per-channel-per-minute** with
a consistency guard, because the manifest-only signal can't catch the
"disk has all 3 channels for one part of the day, recorder cut out for
another" partial-coverage case (~38% of CLIF days).

Open design questions:

1. **Sampling resolution for channel-availability** — yearly enough?
   Confirmed (the existing `channel_epoch_scan` resolution catches all
   the cases we know about). Monthly bisection within a known-change
   year is a v2 refinement.
2. **Multi-variant telemetry tiebreaker** — pick the variant with more
   files? Verified that "more files" is approximately right for STBK but
   `tele_noss` had fewer files yet better coverage (100% vs 99.93%).
   Heuristic acceptable for v1; can swap to "best coverage" later if
   needed.
3. **Consistency guard** for variant switching at minute boundaries —
   pending; v1 will pick one variant for the whole day per channel.

## Test environment status

| Day | Bucket | Source mix | Last conversion |
|---|---|---|---|
| STBK 2022-10-23 | true_b2_canonical | 1440 disk (CHZ only) + 1441 tele_ss + 1440 tele_noss | 450 traces/chan (verified bug reproduction) |
| STBK 2023-01-08 | clean_control | 1440 disk + 1440 tele_ss | not yet converted in test env |
| HOLS 2018-06-15 | clean_baseline | 1440 disk EchoPro + 29 tele_ss + 29 triggered | clean |
| HOLS 2018-01-04 | hols_2018_severe_frag | 1440 disk + 85 tele_ss | clean (so we picked the wrong day — production was fine) |
| BEST 2025-03-30 | partial_disk_fragmented | 408 disk + 406 tele_ss + 69 triggered | not yet converted in test env |

## Open work

1. **`investigate_unmatched.py` results** — in flight; will close the
   "what are these 4 M files?" question.
2. **Planner implementation** — `coverage_planner.py` consuming the
   three artefacts (`categorize_source` + `_discovery` + `_epochs`).
3. **More test-env days** — pick representatives from CLIF buckets so
   we test the planner against the "per-minute stitching" path.
4. **Phase3 integration** — modify `phase3_driver.py` to call the
   coverage planner instead of `cross_source.select_files_for_day`.
   Keep cross_source as fallback for any day where the new artefacts
   aren't built.
5. **Re-conversion path** — once planner + verifier pass on all test
   days, only then start the actual VW re-conversion.

## Reference

- Memory: `feedback-verify-before-claiming`, `feedback-use-station-dbs-first`,
  `project-eqserver-telemetry-only-via-network`, `project-class-a-vs-class-b-framing`.
- Related register: `docs/scan1_recovery_register.md`.
- Source: `metadata/source_stats/` (this entire doc's findings derive from there).

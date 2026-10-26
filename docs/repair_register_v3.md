# VW/VX repair register — the one list (2026-10-26)

**What this is:** the comprehensive, evidence-ranked list of already-
converted data that v3 should re-convert once it passes its gate. It
merges the three evidence sources into one actionable register: the QA
audit campaign (6,775 sampled days, completed 2026-10-17), the v1 defect
catalogue (`docs/v1_lt_defects_and_v2_resweep_scope.md`, 12 classes), and
the sweep recovery registers. Repair unit = whole day-file swap; the
auditor verifies before any overwrite; override gate + ledger + storage
versioning make every repair recorded and reversible.

**Honesty note:** the audit sampled days, it did not read every day. The
"lost hours" below are what the SAMPLE found; whole-station-year
re-conversion (the chosen repair method) recovers the unsampled losses
too, so sampling bias does not reduce repair completeness — it only means
the totals below are floors, not totals.

## Tier 0 — the dress rehearsal (runs FIRST; doubles as proof of v3)

| Unit | Why |
|---|---|
| VW BEST all years | replication benchmark — v2/v3 lineage proof |
| VW WLSH all years (esp. 2018–2023) | the Class-2 poster child: audit found 1,055 h lost in 2019 alone (22 of 41 sampled days >5% loss); bundles carry the only N/E |
| VW STBK 2022-10-23 (+ its year) | the named B2 alternation day (406 × 1.6 s slivers) |
| VW CLIF 2018-01-16 (+ its year) | Class 7 STEIM2 spike day |

Acceptance: v3 output ≥ v1 coverage everywhere, timestamps as-recorded,
zero bad samples, independent witness agrees on channel/sample counts.

## Tier 1 — whole station-year re-converts, by audited loss (floors)

From the audit campaign (last-record dedup, sampled days):

| Station-year | Sampled loss | Signal |
|---|---|---|
| VW WLSH 2019 | 1,055 h | Class 2 (also Tier 0) |
| VW DDNE 2019 / 2017 / 2018 | 755 / 146 / 111 h | Class 2 (disk Z-only eras; up to ~18 ks/chan/day) |
| VW HOGN 2019 | 490 h | Class 2 / selection |
| VW SGWU 2019 | 321 h | RT130 per-channel cohort drop |
| VW HOLS 2022 / 2021 / 2020 / 2023 | 189 / 143 / 109 / 34 h | Class 4 clip + selection (HOLS 2022/2023 also lost 716 days to the clip bug — recovered in v1 but re-verify) |
| VW TRPU 2016 / 2019 / 2022 / 2021 | 145 / 53 / 39 / 29 h | RT130 cohort |
| VW NARR 2022 / 2023 / 2021 | 124 / 58 / 40 h | telemetry-primary selection |
| VW BRTH 2018 / 2019 / 2020 | 109 / 108 / 32 h | selection |
| VW WDSD 2019 / 2018 | 108 / 61 h | selection (single-channel stubs) |
| VW DDSW 2017 / 2021 / 2019 | 66 / 62 / 39 h | selection |
| VW DDWK 2017 / 2021 | 57 / 33 h | selection |
| VW MARD 2022 | 48 h | selection |
| VW LOYU 2019 | 48 h | RT130 cohort |
| VW LRNW 2020 | 36 h | selection |

Plus, regardless of audited loss (defect-class membership alone):

- **Class 1 (side-load fragmentation):** every station-year written in
  the 2026-06-01→12 window (~320 unit-years, ALL in-window stations) —
  fragmentation, not loss; repair = clean re-convert, apply skips where
  byte-equal.
- **Class 3 (pre-midnight-fix):** the ~32 unit-years converted before the
  boundary fix landed 2026-06-02 (BEST + BRIG + BRTH cohort + first ~29
  sweep units) — ~12–22 s lost at each day start.
- **Class 8/9 mislabels:** legacy `DL*`/`SUM` channel codes inside v1-era
  LT years (LRWS/LRNW 2021, BRIG 2020); HDDL pre-2018 and MRDN
  post-2018-04-27 label fixes.
- **Class 11/12:** SOMU dual-sensor Trillium drop; KRAN 2012
  accelerometer-only year (decide keep/drop with identity session).
- **VX:** the 32 VX unit-years (Moe/ALLA 2012 Echo) converted through the
  v1 queue before Echo channel recognition was proven — re-verify, and
  re-convert any day the auditor flags.

## Tier 2 — scattered single days (audit-flagged, outside Tier-1 years)

A few hundred days across otherwise-healthy station-years (full list is
mechanical: non-PASS days in
`/mnt/seiscomp_staging/qa_verify/2026-10-16/results.jsonl`, last record
per day, excluding Tier-0/1 years). Convert these as per-day repair runs
after Tier 1.

## Not repairs (do not convert)

- **Class A** — real source gaps: the recorder never captured it; nothing
  to recover. The auditor's union-gap evidence distinguishes these.
- Timing QUALITY (HOLS ~20–30 s absolute offset, LKHRT drift): converted
  as-timestamped by ruling; any correction is a separate post-conversion
  product under different location codes, never a repair of these files.

## Sequencing (operator decision pending)

Recommendation on record: Tier 0 before DU (it is the dress rehearsal);
Tiers 1–2 after DU and before decommission (~Jan 2027), because DU is the
only data that exists nowhere else. Operator may instead pull Tier 1
before DU to flush out more issues — this register serves either order.

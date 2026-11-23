# VW + VX re-sweep with the v3 pipeline — plan (2026-11-18, for Codex review)

Dan agreed the shape 2026-11-18. Deadline: EqServer origin storage closes ~Jan 2027.

## Goal

Every VW and VX station-year re-converted once with the DU-proven v3 pipeline;
the long-term archive (LT) ends up with the better of (v1 output, v3 output)
for every day-channel. Same >99%-of-available-data bar as DU.

## Approach (simple, generic, rerunnable)

1. **Re-convert everything** into shared staging, as DU was: VX first (18
   stations; never swept apart from 32 Moe/ALLA 2012 units done on v1), then
   VW worst-first (41 stations, ~325 station-years, ~12 TB). The four
   2026-11-17 VW additions (WALM, BRAD, PEGU, PEGM) are already in release
   v2.8 and are not repeated.
2. **Let promotion decide per day-channel**, unchanged machinery: LT absent →
   write; equal → skip; staged has more samples → override, held, then Dan's
   overlap rule (staged supersedes where it has more data; old file renamed,
   never deleted).
3. The repair list is therefore the promotion's held/override set — not the
   October audit sample, which cannot see losses outside its sampled days.

## Gate before any bulk run: names must match LT

VW is already in LT under established names (CHZ@00 etc.). If v3 writes a
different channel or location code for the same data, promotion stores it
twice instead of repairing it. So, per VW/VX station:

- convert 1–2 test days per recorder epoch (v3, scratch output);
- compare output channel/location codes with LT's for those days;
- any mismatch → fix the plan (adopt LT's names) before conversion.
Known deltas already recorded: WLSH 2018 CHO (v3) vs CDO (LT); WLSH 2019 HH*
vs HQ*. RT130 borehole stations keep HH1/HH2/HHZ (operator decision
2026-06-21). Minimus (DDBE/DDWB/SCM2) per-channel files are data.

## Order of work

1. Finish DU: v2.8 run → Codex → promote; merge `du-release-v2.5` into
   `workflow-v3` (after dev1's 12 local pin commits are pushed); DU
   provenance snapshot.
2. VW/VX plans regenerated from Level-1 + current LT names (naming gate above).
3. Codex review of plans + name-gate results.
4. VX conversion → Codex spot-check → promote.
5. VW conversion, worst audit years first → Codex spot-check → promote.

## Open questions for Codex

- **Equal-count, wrong-content days.** decide() compares sample counts. A v1
  LT day with the right number of samples but wrong values or timing (the
  2026-06 side-load window; fragmented traces) would be SKIPPED, not
  repaired. Is a content comparison needed for some window (e.g. the
  ~320 side-load station-years), or is the count rule enough for >99%?
- **Live-feed years.** VW 2025–26 days already hold live SeedLink data in LT;
  overlaps will be held. Same per-unit rule as DU, or exclude years after
  each station's live-feed start?
- **Naming oracle.** Should plans take channel names directly from LT's
  existing day-files (per station and epoch), rather than generating them
  and comparing afterwards?
- Anything in the VW/VX recorder mix (RT130 per-channel files, Minimus,
  `.wrno` files, mixed-separator telemetry, 2012 Echo) that the DU proofs did
  not exercise and that needs a proof day before bulk conversion.

## Rulings and results so far

- **2026-11-23, fresh indexes (Codex F4): done** for all 59 VW/VX stations at
  release v2.8's parser; DBs on the share at
  `/mnt/seiscomp_staging/eqserver_station_dbs/vwvx_2026-11-19/`.
- **2026-11-23, `.wrno` census (Codex F5): done.** 10,647 files named before
  2012; every header carries the same bogus 1989/1999 date. Shifted +1024
  weeks, the 1999 files start 2019-04-07 (the GPS rollover): MARD, HDDL, NARR
  and nearly all WPSH duplicate correctly dated files; ~90 BEST/CLIF minutes
  have no normal copy. The 1989 files (6,172 at BRIG) map to no real date.
  **Dan's ruling: exclude ALL pre-2012-dated `.wrno` files.** Lost: ~90
  BEST/CLIF minutes of April 2019 and ~4 days of undatable BRIG files.
  Census: VM `~/v3_test/wrno_census.jsonl`, `wrno_shift.py`.

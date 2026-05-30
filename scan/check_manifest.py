#!/usr/bin/env python3
"""Sanity-check the Level-1 manifest against Phase 2b's classification needs.

Phase 2b's load-bearing assumption: the *bulk* of station-days are 'complete clean'
(exactly 1,440 disk files, one SS, no triggered/wrong-station noise) and can be
classified + their conversion file-list selected directly from the manifest, with
zero further NFS reads. This script verifies that on whatever mini-DB you point it
at, and surfaces the pathological cases the manifest needs to flag.

Stdlib only.
"""
from __future__ import annotations
import argparse
import datetime
import sqlite3
import sys
from collections import Counter

# One SQL pass: per (station, day) summary across all relevant facets.
PER_DAY_SQL = """
SELECT station, dir_year, dir_month, dir_day,
  SUM(CASE WHEN recorder_type='echopro' AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_echopro_ok,
  SUM(CASE WHEN recorder_type='gecko'   AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_gecko_ok,
  SUM(CASE WHEN recorder_type='mseed'   AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_mseed_ok,
  SUM(CASE WHEN source_type='disk' AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_disk_ok,
  SUM(CASE WHEN source_type='telemetry' AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_tele_ok,
  SUM(CASE WHEN role='metadata' THEN 1 ELSE 0 END) AS n_ss,
  SUM(CASE WHEN exclude_reason='triggered' THEN 1 ELSE 0 END) AS n_triggered,
  SUM(CASE WHEN exclude_reason='wrong_station' THEN 1 ELSE 0 END) AS n_wrong_sta,
  SUM(CASE WHEN exclude_reason='single_channel' THEN 1 ELSE 0 END) AS n_single_chan,
  SUM(CASE WHEN exclude_reason='unknown_extension' THEN 1 ELSE 0 END) AS n_unknown_ext,
  COUNT(DISTINCT CASE WHEN source_type='disk' AND exclude_reason IS NULL THEN ss END) AS n_ss_disk,
  COUNT(DISTINCT CASE WHEN source_type='telemetry' AND exclude_reason IS NULL THEN ss END) AS n_ss_tele,
  COUNT(DISTINCT CASE WHEN source_type='disk' AND exclude_reason IS NULL THEN hhmm END) AS n_hhmm_disk,
  COUNT(DISTINCT CASE WHEN source_type='telemetry' AND exclude_reason IS NULL THEN hhmm END) AS n_hhmm_tele,
  -- Union of disk OR tele HHMM coverage — drives cross-source recovery
  COUNT(DISTINCT CASE WHEN exclude_reason IS NULL AND source_type IN ('disk','telemetry') THEN hhmm END) AS n_hhmm_union
FROM files
WHERE dir_year > 0
GROUP BY station, dir_year, dir_month, dir_day
"""

NEAR, PARTIAL = 5, 60
TELE_PRIMARY_DISK_NOISE = 100  # tolerate this many disk stragglers in tele-primary days
GURALP_DAY_FILES = 4320        # 3 channels × 1440 minutes for Guralp/Minimus class
MAX_NORMAL_SS = 10             # ssd above this = failing recorder, not normal power-cycling
                                # (operator: normal EchoPro power-cycling is 2-5 sessions/day;
                                # BRIG 2020-03-31 onset of 3-month failure had ssd=33+)

# Per-station recorder-class override.
# Stations annotated as 'minimus' (Guralp Radian + Minimus borehole architecture) emit
# one mseed file per channel per minute, which the default parser flags as
# `exclude_reason=single_channel`. For these stations the single-channel files ARE the
# data, not a discard. Authoritative list (per uom_seismic_metadata historical
# reconciliation 2026-05-27): DDBE, DDWB, SCM2. Override-aware classify branches use
# the `n_single_chan` count as the dataset rather than excluding it.
MINIMUS_STATIONS_DEFAULT = {"DDBE", "DDWB", "SCM2"}


def classify(r, station_class=None):
    """Classify a station-day. Branches by dominant recorder because the rules differ:

    `station_class` is an optional per-station recorder-class override (set from the
    registry's `recorder_types` field). The Minimus override is the most important:
    those stations have all their files flagged as `single_channel` by the parser,
    so the default classifier sees zero data and calls it `skip_empty`. The override
    treats those single-channel files as the actual data.

    - EchoPro: SS is a session id (1 SS = single session; 2+ SS = power-cycling restarts,
      both first-class clean per the operator's normal-running profile).
    - Gecko:   disk filenames have NO SS field, so the SS test never fires for Gecko-clean
      days. Use file count + HHMM coverage only.
    - mseed-dominant (provisional: Guralp Radian + Minimus): emits one file per channel
      per minute (~4,320/day), and despite space-separated filenames is NOT telemetry —
      it's manually uploaded mseed dressed up to look like tele (user-confirmed 2026-05-26).

    Cross-source recovery: when disk and tele are each partial but their HHMM union is
    near-complete, the day is recoverable by merging sources (disk wins where present,
    tele fills gaps).
    """
    nd, nt = r["n_disk_ok"], r["n_tele_ok"]
    ne, ng, nm = r["n_echopro_ok"], r["n_gecko_ok"], r["n_mseed_ok"]
    ssd = r["n_ss_disk"]
    hhd, hht = r["n_hhmm_disk"], r["n_hhmm_tele"]
    hhu = r["n_hhmm_union"]
    n_singlechan = r["n_single_chan"]  # parser-excluded single-channel mseed; IS the data for Minimus

    # ---- Minimus override: single-channel mseed IS the data, not a discard ----
    # Per-channel Minimus emits ~3 channels × 1440 minutes = 4,320 files/day.
    if station_class == "minimus":
        total = n_singlechan
        if total >= (GURALP_DAY_FILES - 3 * PARTIAL):
            return "clean_minimus_perchan"
        if total >= (GURALP_DAY_FILES // 2):
            return "near_clean_minimus"
        if total > 0:
            return "partial_minimus"
        return "skip_empty"

    rec_counts = {"echopro": ne, "gecko": ng, "mseed": nm}
    dominant = max(rec_counts, key=rec_counts.get) if any(rec_counts.values()) else None

    # ---- Gecko branch (no SS expected for Gecko disk filenames) ----
    if dominant == "gecko":
        if nd == 1440 and hhd == 1440:
            return "clean_gecko_disk"
        if nd >= (1440 - NEAR) and hhd >= (1440 - NEAR):
            return "near_clean_gecko_disk"
        if nd >= (1440 - PARTIAL) and hhd >= (1440 - PARTIAL):
            return "near_clean_gecko_threshold"
        if nd <= TELE_PRIMARY_DISK_NOISE and nt >= (1440 - PARTIAL) and hht >= (1440 - PARTIAL):
            return "clean_telemetry_primary"
        if hhu >= (1440 - PARTIAL):
            return "clean_cross_source_recovery"
        # Source-disagreement gate (Option B, 2026-05-29): if both disk and tele
        # have substantial files but their HHMM overlap is low, the cross-source
        # merge has no consistent pattern — pathological per the operator's
        # framing (partials are fine, *random* tele/disk distribution is not).
        n_overlap = hhd + hht - hhu
        if hhd >= 60 and hht >= 60 and n_overlap / min(hhd, hht) < 0.5:
            return "partial_source_disagree"
        if 0 < nd < (1440 - PARTIAL):
            return "partial_gecko_disk"
        if nd == 0 and nt > 0:
            return "partial_telemetry_only"
        return "other"

    # ---- mseed-dominant (Guralp/Minimus class — provisional) ----
    # NOTE: per CLAUDE.md, the source_type split (disk vs tele) is meaningless for
    # this class — operator-inserted spaces fake a "tele" label on manually uploaded
    # mseed. Until we have a per-station Guralp/Minimus annotation, classify by total.
    if dominant == "mseed":
        total = nd + nt
        if total >= (GURALP_DAY_FILES - 3 * PARTIAL):
            return "clean_mseed_perchan"   # ~3 chan × 1440 min, provisional
        if total >= (GURALP_DAY_FILES // 2):
            return "partial_mseed_perchan"
        return "partial_mseed_other"

    # ---- EchoPro branch (default) ----
    if nd == 1440 and ssd == 1 and hhd == 1440:
        return "clean_disk_1ss"
    # Multi-session days are CLEAN if coverage is preserved, no matter how many sessions.
    # HOLS-style: ssd may be 30-60 due to power-cycling, but hhd>=1440 (and nd often
    # >1440 because of session-boundary minute duplication) — data is fully captured.
    if nd >= (1440 - PARTIAL) and ssd >= 2 and hhd >= (1440 - PARTIAL):
        return "clean_disk_multi_ss"
    if nd >= (1440 - NEAR) and ssd == 1 and hhd >= (1440 - NEAR):
        return "near_clean_disk_1ss"
    if nd >= (1440 - PARTIAL) and ssd == 1:
        return "near_clean_disk_threshold"
    # Failing recorder = many sessions AND coverage loss together
    # (high ssd alone with full coverage = noisy but clean; the LOSS is what's pathological)
    if ssd > MAX_NORMAL_SS and hhd < (1440 - PARTIAL):
        return "failing_recorder_disk"
    if nd <= TELE_PRIMARY_DISK_NOISE and nt >= (1440 - PARTIAL) and hht >= (1440 - PARTIAL):
        return "clean_telemetry_primary"  # NARR-style: USB pending upload
    if hhu >= (1440 - PARTIAL):
        return "clean_cross_source_recovery"  # disk+tele union covers the day
    # Source-disagreement gate (Option B, 2026-05-29): see gecko branch.
    n_overlap = hhd + hht - hhu
    if hhd >= 60 and hht >= 60 and n_overlap / min(hhd, hht) < 0.5:
        return "partial_source_disagree"
    if 0 < nd < (1440 - PARTIAL):
        return "partial_disk"
    if nd == 0 and nt > 0:
        return "partial_telemetry_only"
    if nd == 0 and nt == 0:
        return "skip_empty"
    return "other"


CLEAN_CATEGORIES = {
    # EchoPro
    "clean_disk_1ss", "clean_disk_multi_ss",
    "near_clean_disk_1ss", "near_clean_disk_threshold",
    # Gecko
    "clean_gecko_disk", "near_clean_gecko_disk", "near_clean_gecko_threshold",
    # mseed-dominant (Guralp/Minimus class, provisional)
    "clean_mseed_perchan",
    # Minimus override (authoritative per-station class — preferred over generic mseed)
    "clean_minimus_perchan", "near_clean_minimus",
    # Source-agnostic clean cases
    "clean_telemetry_primary", "clean_cross_source_recovery",
    # Option B (2026-05-29): partial-coverage days ARE convertible — the recorder
    # captured what it captured; the resulting SDS just has fewer minutes. Pass 1
    # only excludes days where merging is genuinely uncertain (failing_recorder_disk,
    # partial_source_disagree) or impossible (skip_empty, other).
    "partial_disk", "partial_gecko_disk",
    "partial_telemetry_only", "partial_minimus",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="path to mini manifest sqlite")
    ap.add_argument("--show-bad", type=int, default=10, help="how many anomaly examples to show")
    ap.add_argument("--minimus-stations", default=",".join(sorted(MINIMUS_STATIONS_DEFAULT)),
                    help="comma-separated stations to classify with the Minimus override "
                         f"(default: {','.join(sorted(MINIMUS_STATIONS_DEFAULT))})")
    args = ap.parse_args()
    minimus_set = {s.strip() for s in args.minimus_stations.split(",") if s.strip()}

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(PER_DAY_SQL))
    if not rows:
        print("manifest is empty — nothing to classify"); return

    classifications = Counter()
    per_station_clean = Counter()
    per_station_total = Counter()
    pathological = []    # disk has multiple SS but NO triggered flag -> untagged-triggered candidates
    high_wrong_sta = []  # >0 wrong_station files
    excess_tele = []     # tele >> 1440 even after triggered excluded
    unknown_ext_days = []
    for r in rows:
        station_class = "minimus" if r["station"] in minimus_set else None
        c = classify(r, station_class=station_class)
        classifications[c] += 1
        sta = r["station"]
        per_station_total[sta] += 1
        if c in CLEAN_CATEGORIES:
            per_station_clean[sta] += 1
        # Pathological-day signal: failing-recorder pattern = multi-SS thrashing
        # AND coverage loss together. Multi-SS alone (e.g. HOLS's noisy but complete
        # days) is NOT failing — the data is captured despite session-boundary noise.
        # n_triggered is *not* part of the filter — a failing recorder may also be
        # writing triggered files.
        if r["n_ss_disk"] > MAX_NORMAL_SS and r["n_hhmm_disk"] < (1440 - PARTIAL):
            pathological.append((sta, r["dir_year"], r["dir_month"], r["dir_day"],
                                 r["n_ss_disk"], r["n_disk_ok"]))
        if r["n_wrong_sta"] > 0:
            high_wrong_sta.append((sta, r["dir_year"], r["dir_month"], r["dir_day"], r["n_wrong_sta"]))
        if r["n_tele_ok"] > 1500 and r["n_disk_ok"] >= 1400:
            excess_tele.append((sta, r["dir_year"], r["dir_month"], r["dir_day"],
                                r["n_disk_ok"], r["n_tele_ok"]))
        if r["n_unknown_ext"] > 0:
            unknown_ext_days.append((sta, r["dir_year"], r["dir_month"], r["dir_day"], r["n_unknown_ext"]))

    n_days = len(rows)
    clean = classifications["clean_disk_1ss"]
    print(f"\n=== Day classifications across {n_days} station-days ===")
    for c, n in classifications.most_common():
        mark = " *" if c in CLEAN_CATEGORIES else ""
        print(f"  {n:>5} ({n*100/n_days:5.1f}%)  {c}{mark}")

    print(f"\n=== Headline ===")
    print(f"  strict-clean (1,440 disk + 1 SS):       {clean}/{n_days} = {clean*100/n_days:.1f}%")
    total_clean = sum(classifications[c] for c in CLEAN_CATEGORIES)
    print(f"  CLEAN (* above — all first-class recoverable):  {total_clean}/{n_days} = {total_clean*100/n_days:.1f}%")

    print(f"\n=== Per-station clean-rate (any first-class CLEAN category) ===")
    for sta in sorted(per_station_total):
        c, t = per_station_clean[sta], per_station_total[sta]
        print(f"  {sta:6s}  {c:>3d} / {t:>3d}  clean ({c*100/t:5.1f}%)")

    print(f"\n=== Failing-recorder days (ssd >= {MAX_NORMAL_SS}) ===")
    print(f"  (Normal EchoPro power-cycling is 2-10 SS/day; above that = failing.)")
    print(f"  count: {len(pathological)}")
    for row in pathological[:args.show_bad]:
        print(f"    {row[0]:6s}  {row[1]:04d}-{row[2]:02d}-{row[3]:02d}  ss_count={row[4]}  n_disk_ok={row[5]}")

    # --- Failing-recorder episode detector ----------------------------------
    # Group consecutive ssd>=10 days per station into episodes (CRJN-style failing
    # recorder, BRIG-style intermittent crashes). Allows 1 'good' day between
    # degraded days in the same episode (typical for intermittent failures).
    FAILING_SSD = 10
    GAP_TOLERANCE_DAYS = 2
    failing = sorted(
        (sta, datetime.date(y, m, d), ssd, ndo)
        for sta, y, m, d, ssd, ndo in pathological
        if ssd >= FAILING_SSD
    )
    episodes = []
    for sta, day, ssd, ndo in failing:
        if (episodes and episodes[-1]["station"] == sta
                and (day - episodes[-1]["end"]).days <= GAP_TOLERANCE_DAYS):
            ep = episodes[-1]
            ep["end"] = day; ep["days"] += 1
            ep["max_ssd"] = max(ep["max_ssd"], ssd)
            ep["min_disk"] = min(ep["min_disk"], ndo)
        else:
            episodes.append({"station": sta, "start": day, "end": day, "days": 1,
                             "max_ssd": ssd, "min_disk": ndo})
    if episodes:
        print(f"\n=== Failing-recorder EPISODES (ssd≥{FAILING_SSD}, grouped within {GAP_TOLERANCE_DAYS} days) ===")
        print(f"  {len(episodes)} episode(s) collapsing {sum(e['days'] for e in episodes)} day(s)")
        for ep in episodes:
            span = (ep["end"] - ep["start"]).days + 1
            print(f"    {ep['station']:6s}  {ep['start']} → {ep['end']}  "
                  f"({ep['days']} bad days over {span}-day span, "
                  f"max_ssd={ep['max_ssd']}, min_disk={ep['min_disk']})")

    if high_wrong_sta:
        print(f"\n=== Wrong-station contamination days: {len(high_wrong_sta)} ===")
        for row in high_wrong_sta[:args.show_bad]:
            print(f"    {row[0]:6s}  {row[1]:04d}-{row[2]:02d}-{row[3]:02d}  n_wrong_sta={row[4]}")

    if excess_tele:
        print(f"\n=== Telemetry-excess days (n_tele_ok > 1500 even after exclusions): {len(excess_tele)} ===")
        for row in excess_tele[:args.show_bad]:
            print(f"    {row[0]:6s}  {row[1]:04d}-{row[2]:02d}-{row[3]:02d}  disk={row[4]} tele={row[5]}")

    if unknown_ext_days:
        print(f"\n=== Days with unknown-extension files: {len(unknown_ext_days)} ===")
        for row in unknown_ext_days[:args.show_bad]:
            print(f"    {row[0]:6s}  {row[1]:04d}-{row[2]:02d}-{row[3]:02d}  n_unknown={row[4]}")


if __name__ == "__main__":
    sys.exit(main())

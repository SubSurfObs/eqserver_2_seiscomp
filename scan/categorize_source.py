#!/usr/bin/env python3
"""categorize_source.py — categorize a station's day-level source mix.

Per (sta, year, month, day), buckets the day by what source kinds are
present and their file counts. Manifest-only signals — no header reads,
no NFS walks. Reads the Level-1 per-station SQLite at
/home/.../station_dbs/VW.<STA>.db.

Output written to metadata/source_stats/<STA>.{json,txt}.

Buckets (per day):
  empty        : no waveform files at all
  A_pure       : disk-only, count >= 1400
  D_pure       : telemetry-only single variant, count >= 1400
  E_multivar   : multi-variant tele (both tele_ss and tele_noss present
                 with substantial counts) — EqServer dispatcher-glitch
                 signature (e.g. STBK 2022-10-23)
  AB_mixed     : both disk and tele significant (>=1000 each) — the
                 documented disk-with-tele-fallback case cross_source.py
                 was designed for
  C_partial    : both partial, total ~1440 (50<=each)
  sparse       : <500 files total (very partial day)
  weird        : doesn't fit any above (unusual count combinations)

Coverage:
  full_disk    : disk count >= 1400 (can produce a full day from disk)
  full_tele    : max(tele_ss, tele_noss) >= 1400 (full from single variant)
  full_any     : at least one source kind covers the day
  full_both    : both kinds available — redundancy

USAGE:
  python3 scan/categorize_source.py STBK
  python3 scan/categorize_source.py CLIF --out-dir metadata/source_stats
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "metadata" / "source_stats"

BUCKET_ORDER = ["A_pure", "D_pure", "E_multivar", "C_partial",
                "AB_mixed", "M_perchan", "M_partial",
                "sparse", "weird", "empty"]


def bucket(n_disk: int, n_tele_ss: int, n_tele_noss: int,
           n_perchan: int = 0) -> str:
    """Classify a station-day by file-source mix.

    n_perchan counts per-channel-per-minute mseed files (Minimus borehole
    pattern: ~4320 files/day = 3 chans x 1440 mins). These carry
    exclude_reason='single_channel' in the manifest so were invisible to
    earlier versions of this classifier — DDBE/SCM2 came out 100% 'empty'.

    A clean Minimus day -> M_perchan; a partial Minimus day -> M_partial.
    Falls through to the existing A/D/E/C/AB/sparse/weird logic when
    n_perchan is negligible.
    """
    n_tele = n_tele_ss + n_tele_noss
    n_data = n_disk + n_tele + n_perchan
    if n_data == 0:
        return "empty"
    # Minimus per-channel takes precedence: ~4320 = clean day; partial
    # is anything substantial under that.
    if n_perchan >= 4000:
        return "M_perchan"
    if n_perchan >= 500:
        return "M_partial"
    if n_tele_ss >= 100 and n_tele_noss >= 100:
        return "E_multivar"
    if n_disk >= 1400 and n_tele == 0:
        return "A_pure"
    if n_disk == 0 and n_tele >= 1400:
        return "D_pure"
    if n_disk + n_tele <= 1600 and 50 <= n_disk and 50 <= n_tele:
        return "C_partial"
    if n_disk >= 1000 and n_tele >= 1000:
        return "AB_mixed"
    if n_data < 500:
        return "sparse"
    return "weird"


def categorize(sta: str, db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
SELECT dir_year, dir_month, dir_day,
       SUM(CASE WHEN source_type='disk' AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_disk,
       SUM(CASE WHEN source_type='telemetry' AND ss IS NOT NULL AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_tele_ss,
       SUM(CASE WHEN source_type='telemetry' AND ss IS NULL AND exclude_reason IS NULL THEN 1 ELSE 0 END) AS n_tele_noss,
       SUM(CASE WHEN exclude_reason = 'single_channel' AND recorder_type = 'mseed' THEN 1 ELSE 0 END) AS n_perchan,
       SUM(CASE WHEN exclude_reason IS NOT NULL THEN 1 ELSE 0 END) AS n_excluded
FROM files
WHERE role != 'metadata'
GROUP BY dir_year, dir_month, dir_day
ORDER BY dir_year, dir_month, dir_day
""").fetchall()
    conn.close()

    by_year_buckets: dict = {}
    by_year_coverage: dict = {}
    all_buckets: Counter = Counter()
    coverage_totals = {"full_disk": 0, "full_tele": 0,
                       "full_any": 0, "full_both": 0}
    total_days = 0

    bucketed_days = []
    for r in rows:
        yr, mo, dy, n_disk, n_tele_ss, n_tele_noss, n_perchan, n_excluded = r
        b = bucket(n_disk, n_tele_ss, n_tele_noss, n_perchan)
        max_tele = max(n_tele_ss, n_tele_noss)
        full_disk = n_disk >= 1400
        full_tele = max_tele >= 1400
        all_buckets[b] += 1
        by_year_buckets.setdefault(yr, Counter())[b] += 1
        yc = by_year_coverage.setdefault(
            yr, {"full_disk": 0, "full_tele": 0,
                 "full_any": 0, "full_both": 0, "total": 0})
        yc["total"] += 1
        if full_disk:
            coverage_totals["full_disk"] += 1
            yc["full_disk"] += 1
        if full_tele:
            coverage_totals["full_tele"] += 1
            yc["full_tele"] += 1
        if full_disk or full_tele:
            coverage_totals["full_any"] += 1
            yc["full_any"] += 1
        if full_disk and full_tele:
            coverage_totals["full_both"] += 1
            yc["full_both"] += 1
        total_days += 1
        bucketed_days.append({
            "date": f"{yr:04d}-{mo:02d}-{dy:02d}",
            "n_disk": n_disk,
            "n_tele_ss": n_tele_ss,
            "n_tele_noss": n_tele_noss,
            "n_perchan": n_perchan,
            "n_excluded": n_excluded,
            "bucket": b,
        })

    # Pick one representative day per bucket for test-env work — first
    # day of each bucket as encountered (deterministic).
    representatives: dict = {}
    for d in bucketed_days:
        b = d["bucket"]
        if b not in representatives:
            representatives[b] = d

    return {
        "station": sta,
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_db": str(db_path),
        "total_days": total_days,
        "buckets_total": dict(all_buckets),
        "coverage_total": coverage_totals,
        "coverage_pct": {
            k: round(100 * v / total_days, 2) if total_days else 0.0
            for k, v in coverage_totals.items()
        },
        "by_year": {
            yr: {
                "total": yc["total"],
                "full_disk": yc["full_disk"],
                "full_tele": yc["full_tele"],
                "full_any": yc["full_any"],
                "full_both": yc["full_both"],
                "buckets": dict(by_year_buckets[yr]),
            }
            for yr, yc in by_year_coverage.items()
        },
        "representatives": representatives,
        # Full day classification list — every day with its bucket and counts.
        # Used by downstream tooling to pick days deterministically by class.
        # ~80 bytes/day; 3000 days = ~250 KB. Acceptable.
        "day_classifications": bucketed_days,
    }


def format_text(s: dict) -> str:
    lines = []
    lines.append(f"# Source categorization: {s['station']}")
    lines.append(f"# Generated: {s['generated_at_utc']}")
    lines.append(f"# Manifest: {s['manifest_db']}")
    lines.append(f"# Total day-entries: {s['total_days']}")
    lines.append("")
    lines.append("## Coverage (% of days)")
    cov = s["coverage_pct"]
    lines.append(f"  full disk available  (>=1400 disk files):   {cov['full_disk']:5.1f}%")
    lines.append(f"  full tele available  (>=1400 single var):   {cov['full_tele']:5.1f}%")
    lines.append(f"  FULL FROM EITHER kind:                       {cov['full_any']:5.1f}%")
    lines.append(f"  full from BOTH (redundancy):                {cov['full_both']:5.1f}%")
    lines.append("")
    lines.append("## Buckets")
    for b in BUCKET_ORDER:
        n = s["buckets_total"].get(b, 0)
        pct = 100 * n / s["total_days"] if s["total_days"] else 0
        lines.append(f"  {b:12s}: {n:6d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("## Per-year breakdown")
    lines.append(f"  {'year':5s}  {'full_disk':10s} {'full_tele':10s} "
                 f"{'full_any':10s} {'A_pure':7s} {'D_pure':7s} "
                 f"{'E_mvar':7s} {'AB_mix':7s} {'weird':6s} | total")
    for yr in sorted(s["by_year"]):
        y = s["by_year"][yr]
        bk = y["buckets"]
        lines.append(f"  {yr:5d}  {y['full_disk']:10d} {y['full_tele']:10d} "
                     f"{y['full_any']:10d} {bk.get('A_pure', 0):7d} "
                     f"{bk.get('D_pure', 0):7d} {bk.get('E_multivar', 0):7d} "
                     f"{bk.get('AB_mixed', 0):7d} {bk.get('weird', 0):6d} | {y['total']}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta")
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--network", default="VW")
    args = ap.parse_args()

    db_path = args.db_dir / f"{args.network}.{args.sta}.db"
    if not db_path.exists():
        print(f"ERROR: manifest DB not found: {db_path}", file=sys.stderr)
        return 2

    print(f"[categorize_source] {args.sta} from {db_path}", flush=True)
    summary = categorize(args.sta, db_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"{args.sta}.json"
    txt_path = args.out_dir / f"{args.sta}.txt"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    txt_path.write_text(format_text(summary))
    print(f"[categorize_source] wrote {json_path}", flush=True)
    print(f"[categorize_source] wrote {txt_path}", flush=True)
    print()
    print(format_text(summary), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

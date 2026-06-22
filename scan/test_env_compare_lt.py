#!/usr/bin/env python3
"""test_env_compare_lt.py — bulk staging-vs-LT comparison for catalogue.

For every day in the test env catalogue with non-empty staging output,
read its SDS day-files and compare against the long-term archive at
/mnt/seiscomp_archive. Classifies each channel as:

  WRITE     - staging exists, LT empty -> apply.py would copy across
  MATCH     - staging and LT have the same sample count (within tolerance)
  OVERRIDE  - staging has materially more samples than LT
  CLIP      - staging has fewer samples than LT (would lose data)
  ERROR     - read error on either side

Aggregates per-bucket verdicts so the test-suite outcome is a single
go/no-go table.

USAGE:
  python3 scan/test_env_compare_lt.py
  python3 scan/test_env_compare_lt.py --stations BEST,STBK --json out.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

STAGING_BASE = Path("/mnt/seiscomp_staging/test_env_classb/staging_sds")
LT_BASE = Path("/mnt/seiscomp_archive")
CATALOGUE_PATH = Path("/home/unimelb.edu.au/dsand/test_env_classb/catalogue.yaml")

# Verdict thresholds (samples). At 250 Hz one minute is 15,000 samples;
# a "match" tolerance of one minute is generous and avoids edge-trim noise.
MATCH_TOLERANCE_SAMPLES = 15000


def trace_summary(path: str):
    from obspy import read
    try:
        st = read(path, format="MSEED")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if not st:
        return {"n_traces": 0, "n_samples": 0, "rate_hz": 0.0}
    return {
        "n_traces": len(st),
        "n_samples": sum(tr.stats.npts for tr in st),
        "rate_hz": float(st[0].stats.sampling_rate),
    }


def compare_channel(sta: str, year: int, doy: int, chan: str):
    chan_dir = f"{chan}.D"
    fname = f"VW.{sta}.00.{chan}.D.{year:04d}.{doy:03d}"
    st_path = STAGING_BASE / f"{year:04d}" / "VW" / sta / chan_dir / fname
    lt_path = LT_BASE / f"{year:04d}" / "VW" / sta / chan_dir / fname
    if not st_path.exists():
        return {"chan": chan, "verdict": "NO_STAGING",
                "staging": None, "lt_exists": lt_path.exists()}
    s = trace_summary(str(st_path))
    if "error" in s:
        return {"chan": chan, "verdict": "ERROR_staging", "error": s["error"]}
    if not lt_path.exists():
        return {"chan": chan, "verdict": "WRITE",
                "staging_samples": s["n_samples"], "lt_samples": 0,
                "rate_hz": s["rate_hz"]}
    lt = trace_summary(str(lt_path))
    if "error" in lt:
        return {"chan": chan, "verdict": "ERROR_lt", "error": lt["error"]}
    diff = s["n_samples"] - lt["n_samples"]
    if abs(diff) <= MATCH_TOLERANCE_SAMPLES:
        verdict = "MATCH"
    elif diff > 0:
        verdict = "OVERRIDE"  # staging has more — apply.py would override LT
    else:
        verdict = "CLIP"      # staging has fewer — would lose LT data
    return {"chan": chan, "verdict": verdict,
            "staging_samples": s["n_samples"], "lt_samples": lt["n_samples"],
            "diff_samples": diff, "rate_hz": s["rate_hz"]}


def compare_day(sta: str, year: int, month: int, day: int):
    doy = date(year, month, day).timetuple().tm_yday
    staging_dir = STAGING_BASE / f"{year:04d}" / "VW" / sta
    if not staging_dir.exists():
        return {"sta": sta, "year": year, "doy": doy,
                "verdict": "NO_STAGING", "channels": []}
    chans = sorted({d.name[:-2] for d in staging_dir.iterdir()
                    if d.is_dir() and d.name.endswith(".D")})
    channel_results = [compare_channel(sta, year, doy, c) for c in chans]

    # Filter out empty-channel-dir artefacts: leftover <CHAN>.D directories
    # from a previous catalogue's sample-rate epoch. A channel result is
    # "real" only if EITHER side has data for THIS specific DOY.
    real_channels = []
    for r in channel_results:
        staging_present = (
            r.get("staging_samples") is not None and r.get("staging_samples", 0) > 0
        )
        lt_present = (
            r.get("lt_samples") is not None and r.get("lt_samples", 0) > 0
        ) or r.get("verdict") in ("WRITE",)
        if staging_present or lt_present:
            real_channels.append(r)

    # Day verdict: worst of REAL channels by priority. If no real channels
    # for either side, the day is genuinely empty -> NO_STAGING.
    severity = {"ERROR_staging": 6, "ERROR_lt": 5, "NO_STAGING": 4,
                "CLIP": 3, "OVERRIDE": 2, "WRITE": 1, "MATCH": 0}
    if real_channels:
        worst = max(real_channels, key=lambda r: severity.get(r["verdict"], 0))
        day_verdict = worst["verdict"]
    elif channel_results:
        day_verdict = "NO_STAGING"
    else:
        day_verdict = "NO_CHANNELS"
    return {"sta": sta, "year": year, "month": month, "day": day,
            "doy": doy, "verdict": day_verdict, "channels": channel_results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", default=None,
                   help="comma-separated station filter")
    ap.add_argument("--buckets", default=None,
                   help="comma-separated bucket-category filter")
    ap.add_argument("--max", type=int, default=None,
                   help="limit to first N catalogue entries (after filters)")
    ap.add_argument("--json", default=None,
                   help="write JSON output to this path")
    args = ap.parse_args()

    import yaml
    entries = yaml.safe_load(CATALOGUE_PATH.read_text()) or []
    if args.stations:
        wanted = set(args.stations.split(","))
        entries = [e for e in entries if e["sta"] in wanted]
    if args.buckets:
        wanted = set(args.buckets.split(","))
        entries = [e for e in entries if e.get("category") in wanted]
    if args.max:
        entries = entries[: args.max]

    print(f"[compare_lt] {len(entries)} catalogue entries to check", flush=True)
    results = []
    by_verdict = {}
    by_bucket = {}
    for i, e in enumerate(entries, 1):
        sta, y, m, d = e["sta"], e["year"], e["month"], e["day"]
        bucket = e.get("category", "")
        r = compare_day(sta, y, m, d)
        r["category"] = bucket
        results.append(r)
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
        by_bucket.setdefault(bucket, {})[r["verdict"]] = \
            by_bucket.setdefault(bucket, {}).get(r["verdict"], 0) + 1
        if i % 20 == 0 or i == len(entries):
            print(f"  [{i}/{len(entries)}] {sta} {y}-{m:02d}-{d:02d} -> {r['verdict']}",
                  flush=True)

    print(f"\n=== Verdict totals ===")
    for v, n in sorted(by_verdict.items(), key=lambda x: -x[1]):
        pct = 100 * n / len(entries) if entries else 0
        print(f"  {v:18s} {n:>4} ({pct:5.1f}%)")

    print(f"\n=== Verdict by bucket category ===")
    print(f"  {'bucket':32s} {'total':>5s} " +
          "  ".join(f"{v:9s}" for v in ["MATCH", "WRITE", "OVERRIDE", "CLIP", "NO_STAGING", "ERROR_staging", "ERROR_lt"]))
    for bucket in sorted(by_bucket):
        d = by_bucket[bucket]
        total = sum(d.values())
        cells = [f"{d.get(v, 0):9}" for v in ["MATCH", "WRITE", "OVERRIDE", "CLIP", "NO_STAGING", "ERROR_staging", "ERROR_lt"]]
        print(f"  {bucket:32s} {total:>5} " + "  ".join(cells))

    if args.json:
        Path(args.json).write_text(json.dumps({
            "n_entries": len(entries),
            "by_verdict": by_verdict,
            "by_bucket": by_bucket,
            "results": results,
        }, indent=2))
        print(f"\nWritten: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

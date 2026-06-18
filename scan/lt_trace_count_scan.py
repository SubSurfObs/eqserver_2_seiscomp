#!/usr/bin/env python3
"""lt_trace_count_scan.py — find LT day-files with abnormal trace counts.

For Class B re-conversion scoping (Issue 12). Walks LT for each station
in scope, reads each day-file headers-only, counts ObsPy traces.

Fingerprint:
- 1 trace = clean (current engine output, or unaffected EchoPro)
- 2-10 traces = small real outages (probably OK)
- >10 traces = side-load stale-engine signature (Class B candidate)
- >100 = definite side-load contamination

Per (sta, year): report total day-files vs heavy (>10) vs definite (>100).

USAGE:
  python3 scan/lt_trace_count_scan.py \\
      --lt-root /mnt/seiscomp_archive \\
      --net VW \\
      --year-min 2012 --year-max 2025 \\
      --out /tmp/lt_trace_count_scan.txt
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")
from obspy import read


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lt-root", type=Path, required=True)
    ap.add_argument("--net", default="VW")
    ap.add_argument("--year-min", type=int, default=2012)
    ap.add_argument("--year-max", type=int, default=2025)
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    # Per-station-year accumulator
    sy_total: dict[tuple[str, int], int] = defaultdict(int)
    sy_heavy: dict[tuple[str, int], int] = defaultdict(int)
    sy_definite: dict[tuple[str, int], int] = defaultdict(int)
    sy_clean: dict[tuple[str, int], int] = defaultdict(int)
    sy_max: dict[tuple[str, int], int] = defaultdict(int)
    read_errors = 0
    t0 = time.time()

    fh = sys.stdout if args.out == "-" else open(args.out, "w")
    fh.write(f"# LT trace-count scan — finding Class B candidates\n")
    fh.write(f"# net={args.net}  years={args.year_min}-{args.year_max}\n")
    fh.write("# Bucket per file: 1=clean, 2-10=light, 11-100=heavy, >100=definite\n")
    fh.write("#" + "-" * 70 + "\n")

    for year_dir in sorted(args.lt_root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = int(year_dir.name)
        if not (args.year_min <= year <= args.year_max):
            continue
        net_dir = year_dir / args.net
        if not net_dir.is_dir():
            continue
        for sta_dir in sorted(net_dir.iterdir()):
            if not sta_dir.is_dir():
                continue
            sta = sta_dir.name
            for cha_dir in sorted(sta_dir.iterdir()):
                if not cha_dir.is_dir():
                    continue
                for f in sorted(cha_dir.iterdir()):
                    if not f.is_file():
                        continue
                    try:
                        st = read(str(f), headonly=True)
                        n = len(st)
                    except Exception:
                        read_errors += 1
                        continue
                    key = (sta, year)
                    sy_total[key] += 1
                    if n == 1:
                        sy_clean[key] += 1
                    elif n > 100:
                        sy_definite[key] += 1
                        sy_heavy[key] += 1
                    elif n > 10:
                        sy_heavy[key] += 1
                    if n > sy_max[key]:
                        sy_max[key] = n
        # Progress beacon per year
        if args.out != "-":
            print(f"  done {year}: {len(sy_total)} (sta,year) tuples so far  "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)

    fh.write("\nPer-(sta,year):  total_files  clean(=1)  heavy(>10)  definite(>100)  max_traces\n")
    candidates = []
    for key in sorted(sy_total):
        sta, year = key
        total = sy_total[key]
        clean = sy_clean[key]
        heavy = sy_heavy[key]
        definite = sy_definite[key]
        maxn = sy_max[key]
        marker = ""
        if definite >= 5 or (definite > 0 and definite / total > 0.5):
            marker = "  !! CLASS-B-LIKELY"
            candidates.append((sta, year, total, definite, maxn))
        elif heavy / total > 0.5:
            marker = "  ?  heavy-fragmentation"
        fh.write(f"  {sta:8s} {year}   total={total:4d}  clean={clean:4d}  "
                 f"heavy={heavy:4d}  definite={definite:4d}  max={maxn:5d}{marker}\n")

    fh.write(f"\n# Read errors: {read_errors}\n")
    fh.write(f"# Class-B-likely station-years: {len(candidates)}\n")
    if candidates:
        fh.write("# (sta, year, total_files, definite_count, max_traces_in_any_file):\n")
        for sta, year, total, definite, maxn in candidates:
            fh.write(f"  {sta} {year}  total={total} def={definite} max={maxn}\n")
    fh.write(f"# Elapsed: {(time.time()-t0)/60:.1f} min\n")

    if args.out != "-":
        fh.close()
        print(f"[scan] {len(candidates)} Class-B candidates; out: {args.out}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""fragmentation_blast_radius.py — sample Gecko fragmentation per (sta,year).

For each station × year, randomly samples N day-files (default 10 per
channel), reads ObsPy headers only (no sample decode), counts traces.
Reports per-year heavy-fragmentation rate using the sample as estimate.

Headers-only sampling: ~30 sec per station instead of ~10 min full walk.

USAGE:
  python3 scan/fragmentation_blast_radius.py \\
      --lt-root /mnt/seiscomp_archive \\
      --net VW --stations BRTH,FORG,DDNE \\
      --samples-per-year 10 \\
      --out /tmp/frag_blast.txt
"""
from __future__ import annotations
import argparse
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from obspy import read


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lt-root", type=Path, required=True)
    ap.add_argument("--net", default="VW")
    ap.add_argument("--stations", required=True,
                    help="comma-separated station codes")
    ap.add_argument("--samples-per-year", type=int, default=10,
                    help="random day-files to probe per (sta, year) — "
                         "across all channels combined")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="-")
    args = ap.parse_args()
    random.seed(args.seed)

    stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    fh = sys.stdout if args.out == "-" else open(args.out, "w")

    fh.write(f"# Fragmentation blast-radius scan\n")
    fh.write(f"# stations: {stations}\n")
    fh.write(f"# Bucket key: number of obspy traces in the day-file.\n")
    fh.write(f"#   bucket=1       — clean (single contig per day)\n")
    fh.write(f"#   bucket=2-10    — light fragmentation (maybe real outages)\n")
    fh.write(f"#   bucket=11-100  — moderate fragmentation\n")
    fh.write(f"#   bucket=>100    — heavy fragmentation (Gecko-bug signature)\n")
    fh.write("#" + "-" * 70 + "\n\n")

    total_files = 0
    total_heavy = 0
    total_read_errors = 0
    t_overall = time.time()

    for sta in stations:
        sta_dist: Counter[int] = Counter()
        per_year_total: Counter[int] = Counter()
        per_year_heavy: Counter[int] = Counter()
        worst_examples: list[tuple[int, str]] = []
        read_errors = 0

        # Walk all years for this station — SAMPLING N day-files per year
        for year_dir in sorted(args.lt_root.iterdir()):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            year = int(year_dir.name)
            sta_dir = year_dir / args.net / sta
            if not sta_dir.is_dir():
                continue
            # Collect all candidate files across all channels for this year
            candidates = []
            for cha_dir in sorted(sta_dir.iterdir()):
                if not cha_dir.is_dir() or not cha_dir.name.endswith(".D"):
                    continue
                for f in sorted(cha_dir.iterdir()):
                    if f.is_file():
                        candidates.append(f)
            # Random sample
            if len(candidates) > args.samples_per_year:
                sample = random.sample(candidates, args.samples_per_year)
            else:
                sample = candidates
            # Probe each sampled file
            for f in sample:
                try:
                    st = read(str(f), headonly=True)
                    n = len(st)
                except Exception:
                    read_errors += 1
                    continue
                sta_dist[_bucket(n)] += 1
                per_year_total[year] += 1
                if n > 100:
                    per_year_heavy[year] += 1
                    worst_examples.append((n, f.name))
                    worst_examples.sort(reverse=True)
                    worst_examples = worst_examples[:5]
                total_files += 1

        total_heavy += sum(per_year_heavy.values())
        total_read_errors += read_errors

        fh.write(f"=== VW.{sta} ===\n")
        fh.write(f"  files scanned: {sum(sta_dist.values())}, "
                 f"read_errors: {read_errors}\n")
        fh.write(f"  trace-bucket distribution:\n")
        for b in sorted(sta_dist):
            fh.write(f"    {b:>10}: {sta_dist[b]} files\n")
        fh.write(f"  per-year heavy/total:\n")
        for year in sorted(per_year_total):
            heavy = per_year_heavy.get(year, 0)
            total = per_year_total[year]
            pct = (heavy / total * 100) if total else 0
            marker = " !!" if pct > 50 else ("  *" if pct > 10 else "   ")
            fh.write(f"   {marker} {year}: heavy={heavy}/{total} ({pct:.0f}%)\n")
        if worst_examples:
            fh.write(f"  top 5 worst files (by trace count):\n")
            for n, name in worst_examples:
                fh.write(f"    {n:5d}  {name}\n")
        fh.write("\n")
        fh.flush()
        if args.out != "-":
            print(f"  done {sta}: heavy={sum(per_year_heavy.values())}, "
                  f"total={sum(per_year_total.values())}", flush=True)

    elapsed = time.time() - t_overall
    fh.write(f"\n=== GRAND TOTAL ===\n")
    fh.write(f"  files scanned: {total_files}\n")
    fh.write(f"  heavy-fragmentation files (>100 traces): {total_heavy}\n")
    fh.write(f"  read errors: {total_read_errors}\n")
    fh.write(f"  elapsed: {elapsed/60:.1f} min\n")

    if args.out != "-":
        fh.close()
    return 0


def _bucket(n: int) -> str:
    if n == 1: return "1"
    if n <= 10: return "2-10"
    if n <= 100: return "11-100"
    if n <= 500: return "101-500"
    return ">500"


if __name__ == "__main__":
    sys.exit(main())

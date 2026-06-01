#!/usr/bin/env python3
"""sweep_status.py — summarise the current state of a running production sweep.

Reads the convert.py main log + convert_done.jsonl + convert_failed.jsonl
+ promote_done.jsonl + held.jsonl and prints a one-screen status summary.

Designed for spot-check use during a long-running sweep:

  python3 scan/sweep_status.py \\
      --convert-log /var/tmp/eqserver_sweep_convert.log \\
      --queue-dir /mnt/seiscomp_staging/eqserver_sweep

Output sections:

  - Headline counts: convert ok / fail / in-flight, promote committed / held /
    skip-empty.
  - Fail density: most-recent-N units' fail rate. Surfaces concerning trends.
  - Recent fails: last 10 failed units with their last-successful-phase3-day
    (helps spot patterns: same calendar month? same recorder type?).
  - Retry list: stations + years that are currently neither in convert_done
    nor have been attempted (just pending), vs ones that failed and need
    a retry pass.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


CONVERT_LINE_RE = re.compile(
    r"\[convert\]\s+\[(\d+)/(\d+)\]\s+(OK|FAIL)\s+(\S+)\s+(\d+)"
)


def parse_convert_log(path: Path) -> list[dict]:
    """Return list of {idx, total, status, sta, year} from convert.py main log."""
    results = []
    if not path.exists():
        return results
    for line in path.open():
        m = CONVERT_LINE_RE.search(line)
        if m:
            results.append({
                "idx": int(m.group(1)),
                "total": int(m.group(2)),
                "status": m.group(3),
                "sta": m.group(4),
                "year": int(m.group(5)),
            })
    return results


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convert-log", required=True,
                    help="path to the convert.py main log (e.g. /var/tmp/eqserver_sweep_convert.log)")
    ap.add_argument("--queue-dir", required=True,
                    help="path to the queue dir (e.g. /mnt/seiscomp_staging/eqserver_sweep)")
    ap.add_argument("--recent-window", type=int, default=20,
                    help="how many most-recent units to compute fail density over")
    args = ap.parse_args()

    convert_log = Path(args.convert_log)
    queue = Path(args.queue_dir)

    log_results = parse_convert_log(convert_log)
    convert_done = read_jsonl(queue / "convert_done.jsonl")
    convert_failed = read_jsonl(queue / "convert_failed.jsonl")
    promote_done = read_jsonl(queue / "promote_done.jsonl")
    held = read_jsonl(queue / "held.jsonl")

    # Headline counts
    n_ok = sum(1 for r in log_results if r["status"] == "OK")
    n_fail = sum(1 for r in log_results if r["status"] == "FAIL")
    total = log_results[-1]["total"] if log_results else 0
    in_flight = max(0, len({(r["sta"], r["year"]) for r in log_results}) - n_ok - n_fail)

    n_promoted = sum(1 for e in promote_done if e.get("action") == "promoted")
    n_skip_empty = sum(1 for e in promote_done if e.get("action") == "skip-empty")
    n_held = len(held)

    print("=" * 70)
    print("SWEEP STATUS")
    print("=" * 70)
    print(f"  convert.py log: {convert_log}")
    print(f"  queue dir:      {queue}")
    print()
    print(f"  Convert:  {n_ok:4d} ok   {n_fail:4d} fail   {in_flight:2d} in-flight   "
          f"of {total} total units")
    print(f"  Promote:  {n_promoted:4d} promoted   {n_skip_empty:4d} skip-empty   {n_held:2d} held")
    print()

    # Fail density (overall + recent)
    n_attempted = n_ok + n_fail
    if n_attempted > 0:
        overall_rate = n_fail / n_attempted * 100
        recent = log_results[-args.recent_window:]
        recent_fails = sum(1 for r in recent if r["status"] == "FAIL")
        recent_attempted = len(recent)
        recent_rate = (recent_fails / recent_attempted * 100) if recent_attempted else 0.0
        print(f"  Fail density (overall):           {n_fail}/{n_attempted} = {overall_rate:5.1f}%")
        print(f"  Fail density (last {recent_attempted:2d} units):     "
              f"{recent_fails}/{recent_attempted} = {recent_rate:5.1f}%")
        if recent_rate >= 20:
            print(f"  !! WARNING: recent fail rate ≥ 20% — consider aborting + investigating.")
        elif recent_rate >= 10:
            print(f"  !! CAUTION: recent fail rate ≥ 10% — watch closely.")
        print()

    # Recent fails detail
    if convert_failed:
        print(f"  Recent failures (last 10):")
        for f in convert_failed[-10:]:
            sta = f.get("sta")
            year = f.get("year")
            rc = f.get("rc")
            elapsed = f.get("elapsed_s", 0)
            last_day = f.get("last_successful_phase3_day", "") or ""
            # Trim the day-line to first 60 chars
            last_day = last_day[:60] + ("..." if len(last_day) > 60 else "")
            print(f"    {sta} {year}: rc={rc} elapsed={elapsed}s  last_ok={last_day}")
        print()

    # Failure clustering by station / by recorder-type-band (gecko year vs echopro year)
    if convert_failed:
        by_sta = Counter(f["sta"] for f in convert_failed)
        print("  Failures by station:")
        for sta, n in by_sta.most_common(10):
            years = [f["year"] for f in convert_failed if f["sta"] == sta]
            print(f"    {sta}: {n} year(s)  {years}")
        print()

    # Retry list
    failed_keys = {(f["sta"], f["year"]) for f in convert_failed}
    if failed_keys:
        print("  Retry list (re-run after main sweep completes):")
        # Group by station for compact display
        by_sta_retry = defaultdict(list)
        for sta, year in failed_keys:
            by_sta_retry[sta].append(year)
        for sta in sorted(by_sta_retry):
            years = sorted(by_sta_retry[sta])
            year_summary = ",".join(str(y) for y in years)
            print(f"    {sta}: {year_summary}")
        print()
        # Compact CLI to re-run all in one go
        stations = sorted(set(s for s, _ in failed_keys))
        ymin = min(y for _, y in failed_keys)
        ymax = max(y for _, y in failed_keys)
        print("  To retry ALL failed units after the main sweep:")
        print(f"    python3 scan/run_production_convert.py --network VW \\")
        print(f"        --stations {','.join(stations)} \\")
        print(f"        --year-min {ymin} --year-max {ymax} \\")
        print(f"        --registry metadata/station_registry.yaml \\")
        print(f"        --staging-sds /mnt/seiscomp_staging/seiscomp_archive \\")
        print(f"        --queue-dir /mnt/seiscomp_staging/eqserver_sweep \\")
        print(f"        --workers 4 --log-dir /var/tmp/eqserver_retry_logs")
        print()

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())

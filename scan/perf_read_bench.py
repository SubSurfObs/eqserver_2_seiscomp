#!/usr/bin/env python3
"""perf_read_bench — time the read+decode of one station-day from a given root.

The read+decode of source minute-files is the measured dominant cost of
conversion (~95% per PERFORMANCE.md), so timing it local-disk vs NFS gives
the baseline gap that staging/prefetch strategies are trying to close.

Reuses qa_verify's file readers (sudspy for .dmx[.gz], ObsPy for mseed/zip)
so the decode work is identical to the auditor's. Single-threaded by design:
the number of interest is per-worker day cost per source, same concurrency
both sides.

Usage:
  perf_read_bench.py --root ~/perf_corpus --sta BEST --date 2022-06-15 --label local
  perf_read_bench.py --root /mnt/eqserver_archive/shared/data/repository/archive \\
                     --sta BEST --date 2022-06-16 --label nfs_cold_assumed

Output: one JSON line (station, date, label, n_files, mb, traces, wall_s,
read-only). Writes nothing except stdout.
"""

import argparse
import json
import sys
import time
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_verify  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True,
                    help="archive root containing <STA>/continuous/Y/M/D")
    ap.add_argument("--sta", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--label", required=True,
                    help="cache/source label, e.g. local / nfs_cold_assumed / nfs_hot")
    args = ap.parse_args()

    d = date_cls.fromisoformat(args.date)
    day_dir = (Path(args.root).expanduser() / args.sta / "continuous"
               / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}")
    files = sorted(p for p in day_dir.iterdir()
                   if p.is_file() and not p.name.endswith(".ss"))
    if not files:
        sys.exit(f"no files under {day_dir}")

    errors, counters = [], {}
    n_traces = 0
    n_bytes = 0
    t0 = time.monotonic()
    for p in files:
        n_bytes += p.stat().st_size
        n_traces += len(qa_verify.read_source_file(str(p), errors, counters))
    wall = time.monotonic() - t0

    print(json.dumps({
        "sta": args.sta, "date": args.date, "label": args.label,
        "root": str(args.root), "n_files": len(files),
        "mb": round(n_bytes / 1e6, 1), "traces": n_traces,
        "read_errors": len(errors), "wall_s": round(wall, 2),
        "files_per_s": round(len(files) / wall, 1),
        "mb_per_s": round(n_bytes / 1e6 / wall, 2),
    }))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Benchmark: Gecko .ms.zip read strategies.

Compares two ways of reading the inner mseed from a Gecko .ms.zip, on real
STBK station-days, to quantify the NFS-seek penalty of opening the ZIP
directly vs reading the whole file into memory first.

  OLD: zipfile.ZipFile(path)        -> central-dir + member seeks hit NFS
  NEW: read whole file -> BytesIO   -> one sequential NFS read, seeks in RAM

Read+parse only (no SDS write) so we isolate the I/O pattern. Runs each
strategy over the same set of days, reports wallclock + per-day rate.

Run on an IDLE VM for clean numbers:
  python3 scan/gecko_read_benchmark.py \
      --db /home/.../station_dbs/VW.STBK.db \
      --station STBK --year 2019 --n-days 20
"""
from __future__ import annotations
import argparse
import io
import os
import sqlite3
import time
import zipfile


def day_files(conn, station, year, n_days):
    """Return list of (date_tuple, [paths]) for the first n_days with data."""
    rows = conn.execute(
        "SELECT DISTINCT dir_month, dir_day FROM files "
        "WHERE station=? AND dir_year=? AND recorder_type='gecko' "
        "  AND source_type='disk' AND exclude_reason IS NULL "
        "ORDER BY dir_month, dir_day",
        (station, year),
    ).fetchall()[:n_days]
    out = []
    for mo, dy in rows:
        paths = [r[0] for r in conn.execute(
            "SELECT path FROM files WHERE station=? AND dir_year=? AND dir_month=? "
            "AND dir_day=? AND recorder_type='gecko' AND source_type='disk' "
            "AND exclude_reason IS NULL ORDER BY path",
            (station, year, mo, dy),
        )]
        out.append(((year, mo, dy), paths))
    return out


def read_old(path):
    """Direct ZipFile open — multiple NFS seeks."""
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith((".ms", ".mseed")):
                return z.read(name)
    return b""


def read_new(path):
    """Read whole file once (sequential), then open ZIP from memory."""
    with open(path, "rb") as fh:
        raw = fh.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if name.endswith((".ms", ".mseed")):
                return z.read(name)
    return b""


def drop_caches():
    """Drop OS page cache so the next read is genuinely cold. Needs sudo.
    Best-effort — if it fails (no sudo), we note it and continue."""
    try:
        os.system("sync && sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null")
        return True
    except Exception:
        return False


def run_strategy(label, reader, days, do_parse):
    from obspy import read
    t0 = time.time()
    total_files = 0
    total_bytes = 0
    for (date_tuple, paths) in days:
        combined = io.BytesIO()
        for p in paths:
            data = reader(p)
            combined.write(data)
            total_files += 1
            total_bytes += len(data)
        if do_parse:
            combined.seek(0)
            if combined.getbuffer().nbytes > 0:
                _ = read(combined, format="MSEED")
    elapsed = time.time() - t0
    n_days = len(days)
    print(f"  [{label:4}] {n_days} days, {total_files} files, "
          f"{total_bytes/1e6:.1f} MB read+{'parse' if do_parse else 'noparse'}: "
          f"{elapsed:.1f}s  ({elapsed/n_days:.2f}s/day)", flush=True)
    return elapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--station", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--n-days", type=int, default=20)
    ap.add_argument("--no-parse", action="store_true",
                    help="skip obspy parse, measure read-only")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    days = day_files(conn, args.station, args.year, args.n_days)
    if not days:
        print(f"No gecko disk days for {args.station} {args.year}")
        return 1
    print(f"[bench] {args.station} {args.year}: {len(days)} days, "
          f"{sum(len(p) for _, p in days)} files total")
    print(f"[bench] parse={'no' if args.no_parse else 'yes'}")

    # COLD comparison: drop the page cache before each strategy so both
    # see the same cold-NFS starting point (no cross-contamination from the
    # other strategy having already cached the files).
    print("\n=== COLD (cache dropped before each) ===")
    dc1 = drop_caches()
    old_cold = run_strategy("OLD", read_old, days, not args.no_parse)
    dc2 = drop_caches()
    new_cold = run_strategy("NEW", read_new, days, not args.no_parse)
    if not (dc1 and dc2):
        print("  (NOTE: cache-drop may have failed — cold numbers unreliable)")

    # WARM comparison: both run back-to-back with cache hot. Isolates the
    # pure CPU/seek-in-RAM cost difference (NFS latency removed).
    print("\n=== WARM (cache hot) ===")
    old_warm = run_strategy("OLD", read_old, days, not args.no_parse)
    new_warm = run_strategy("NEW", read_new, days, not args.no_parse)

    print(f"\n=== Result ===")
    print(f"  COLD: OLD {old_cold:.1f}s vs NEW {new_cold:.1f}s  -> "
          f"NEW is {old_cold/new_cold:.2f}x {'faster' if new_cold < old_cold else 'slower'}")
    print(f"  WARM: OLD {old_warm:.1f}s vs NEW {new_warm:.1f}s  -> "
          f"NEW is {old_warm/new_warm:.2f}x {'faster' if new_warm < old_warm else 'slower'}")
    print(f"\n  COLD is the production-relevant number (real NFS reads). If NEW")
    print(f"  wins big on COLD but ties on WARM, that confirms the gain is from")
    print(f"  collapsing NFS round-trips, exactly as hypothesised.")


if __name__ == "__main__":
    import sys
    sys.exit(main())

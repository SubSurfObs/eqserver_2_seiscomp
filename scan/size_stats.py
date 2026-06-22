#!/usr/bin/env python3
"""size_stats.py — augment channel-epoch output with per-(sta, source_kind,
epoch) file-size percentiles.

Size is a useful Bayesian PRIOR for source selection: a file much smaller
than the epoch median is suspect ("partial minute", "single-channel
fragment"); a file near the epoch median is the expected clean shape.
Not load-bearing — the planner only consults it when the epoch lookup
alone leaves a tie.

Reads `metadata/source_stats/_epochs/<STA>.json` (channel_epoch_scan
output, post-Step-1 bisection) and the per-station manifest DB. Writes
a sibling `_epochs/<STA>.size_stats.json` AND injects a `size_stats`
block into each epoch in the existing `_epochs/<STA>.json` so a single
file remains the authority.

USAGE:
  python3 scan/size_stats.py STBK
  python3 scan/size_stats.py STBK --db-dir /custom/dbs --out-dir custom/_epochs
  python3 scan/size_stats.py --all   # every _epochs/<STA>.json present
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / \
                  "metadata" / "source_stats" / "_epochs"

# Source-kind filename patterns — must mirror channel_epoch_scan.SOURCE_KINDS
# and scan/level1.py:MIXED_SHAPES. Kept here as compiled regexes for the SQL
# post-filter (SQLite's GLOB can't express these grammars compactly).
SOURCE_KIND_REGEXES = {
    "disk_suds":             re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_\d{2}_\w+\.dmx(\.gz)?$"),
    "tele_ss_suds":          re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.dmx(\.gz)?$"),
    "tele_noss_suds":        re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.dmx(\.gz)?$"),
    "disk_mseed":            re.compile(r"^\d{8}_\d{4}_\w+\.ms\.zip$"),
    "tele_ss_mseed":         re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.ms\.zip$"),
    "tele_noss_mseed":       re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ms\.zip$"),
    "tele_underscore_mseed": re.compile(r"^\d{4}-\d{2}-\d{2} \d{4}_\w+\.ms\.zip$"),
    "tele_dasharound_mseed": re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}-\w+\.ms\.zip$"),
    "tele_alldash_mseed":    re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}_\w+\.ms\.zip$"),
}


def _percentile(sorted_values, q):
    """Linear-interpolated percentile q in [0, 100]. sorted_values is sorted."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (q / 100)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def compute_size_stats(conn: sqlite3.Connection, sta: str,
                       source_kind: str, epoch: dict) -> dict:
    """For one (sta, source_kind, epoch), collect file sizes from manifest
    rows whose path basename matches the source_kind regex AND whose
    (dir_year, dir_month, dir_day) falls in [epoch.start, epoch.end] inclusive.
    """
    regex = SOURCE_KIND_REGEXES.get(source_kind)
    if regex is None:
        return {"n_samples": 0, "note": f"unknown source_kind {source_kind!r}"}

    start = date.fromisoformat(epoch["start"])
    end   = date.fromisoformat(epoch["end"])
    # Single-day epochs are common after bisection — widen to ±7 days so we
    # have enough sample mass for percentiles. The user of the percentiles
    # cares about epoch median, not exact boundary handling.
    if (end - start).days < 7:
        from datetime import timedelta
        start = start - timedelta(days=3)
        end   = end + timedelta(days=3)

    # Pull dir-keyed candidate rows from the manifest. Inner-loop regex
    # match keeps SQL trivial and lets the (station, year, month, day) index
    # do the work. Cap at 50k rows per epoch — far more than needed for
    # percentile stability.
    rows = conn.execute(
        "SELECT size_bytes, path FROM files "
        "WHERE station = ? AND role != 'metadata' "
        "  AND ((dir_year > ?) OR (dir_year = ? AND dir_month > ?) "
        "       OR (dir_year = ? AND dir_month = ? AND dir_day >= ?)) "
        "  AND ((dir_year < ?) OR (dir_year = ? AND dir_month < ?) "
        "       OR (dir_year = ? AND dir_month = ? AND dir_day <= ?)) "
        "LIMIT 50000",
        (sta,
         start.year, start.year, start.month, start.year, start.month, start.day,
         end.year,   end.year,   end.month,   end.year,   end.month,   end.day),
    ).fetchall()

    sizes = []
    for sz, path in rows:
        name = path.rsplit("/", 1)[-1]
        if regex.match(name):
            sizes.append(sz)

    if not sizes:
        return {"n_samples": 0}

    sizes.sort()
    return {
        "n_samples": len(sizes),
        "p10": int(_percentile(sizes, 10)),
        "p25": int(_percentile(sizes, 25)),
        "p50": int(_percentile(sizes, 50)),
        "p75": int(_percentile(sizes, 75)),
        "p90": int(_percentile(sizes, 90)),
        "min": int(sizes[0]),
        "max": int(sizes[-1]),
    }


def process_station(sta: str, db_path: Path, epochs_path: Path) -> dict:
    """Augment _epochs/<STA>.json with size_stats per (kind, epoch)."""
    if not epochs_path.exists():
        return {"sta": sta, "skipped": True, "reason": f"no _epochs/{sta}.json"}

    d = json.loads(epochs_path.read_text())
    conn = sqlite3.connect(db_path)

    by_kind = d.get("by_source_kind", {})
    n_epochs = 0
    n_with_data = 0
    for kind, epochs in by_kind.items():
        for epoch in epochs:
            n_epochs += 1
            ss = compute_size_stats(conn, sta, kind, epoch)
            epoch["size_stats"] = ss
            if ss["n_samples"] > 0:
                n_with_data += 1
    conn.close()

    d["size_stats_added_at_utc"] = date.today().isoformat()
    epochs_path.write_text(json.dumps(d, indent=2, sort_keys=True))
    return {
        "sta": sta,
        "n_epochs": n_epochs,
        "n_epochs_with_size_data": n_with_data,
        "out_path": str(epochs_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta", nargs="?", help="Station code, e.g. STBK; omit with --all")
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--network", default="VW")
    ap.add_argument("--all", action="store_true",
                   help="Process every _epochs/<STA>.json under --out-dir")
    args = ap.parse_args()

    if args.all:
        targets = []
        for f in sorted(args.out_dir.glob("*.json")):
            sta = f.stem
            db = args.db_dir / f"{args.network}.{sta}.db"
            if db.exists():
                targets.append((sta, db, f))
    else:
        if not args.sta:
            print("ERROR: provide a station code or --all", file=sys.stderr)
            return 2
        db = args.db_dir / f"{args.network}.{args.sta}.db"
        if not db.exists():
            print(f"ERROR: DB not found: {db}", file=sys.stderr)
            return 2
        ep = args.out_dir / f"{args.sta}.json"
        if not ep.exists():
            print(f"ERROR: epoch JSON not found: {ep}", file=sys.stderr)
            return 2
        targets = [(args.sta, db, ep)]

    for sta, db, ep in targets:
        print(f"\n--- {sta} ---", flush=True)
        r = process_station(sta, db, ep)
        if r.get("skipped"):
            print(f"  skipped: {r['reason']}")
            continue
        print(f"  epochs processed:        {r['n_epochs']}")
        print(f"  epochs with size data:   {r['n_epochs_with_size_data']}")
        print(f"  updated: {r['out_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

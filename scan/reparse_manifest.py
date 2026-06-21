#!/usr/bin/env python3
"""reparse_manifest.py — re-run parse_filename across an existing
per-station manifest DB and UPDATE the parsed-field columns in place.

Use this after extending the level1 grammar (e.g. adding new shape
regexes to MIXED_SHAPES). The DB already has every row (path, size,
mtime) — only the parsed fields (file_year/month/day, hhmm, ss,
filename_station, channel_suffix, flags, station_mismatch, recorder_
type, source_type, role, exclude_reason) need refreshing.

No NFS reads. Pure DB work — minutes not hours.

USAGE:
  python3 scan/reparse_manifest.py /path/to/VW.SGWU.db [--dry-run]
  python3 scan/reparse_manifest.py /path/to/dbdir --all-vw [--dry-run]
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
import time
from pathlib import Path

# Import parse_filename from the canonical source
import importlib.util
ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("level1", ROOT / "scan" / "level1.py")
level1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(level1)
parse_filename = level1.parse_filename


# Columns we re-derive from filename. Path / station / dir_* / size_bytes
# / mtime are NOT touched.
REPARSE_COLS = [
    "recorder_type", "source_type", "role",
    "file_year", "file_month", "file_day",
    "hhmm", "ss", "channel_suffix",
    "filename_station", "station_mismatch",
    "flags", "exclude_reason",
]
UPDATE_SQL = (
    "UPDATE files SET "
    + ", ".join(f"{c} = ?" for c in REPARSE_COLS)
    + ", date_mismatch = ? "
    "WHERE path = ?"
)


def reparse_db(db_path: Path, dry_run: bool = False) -> dict:
    """Re-parse every row in one DB. Returns counts of changes."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    t0 = time.time()
    fields_sql = "path, station, dir_year, dir_month, dir_day, " + \
                 ", ".join(REPARSE_COLS) + ", date_mismatch"
    rows = list(cur.execute(f"SELECT {fields_sql} FROM files"))

    n_total = len(rows)
    n_changed = 0
    n_unparsed_now_parsed = 0
    n_was_excluded_now_clean = 0
    by_new_excl = {}
    update_batch = []

    for row in rows:
        path = row[0]
        sta = row[1]
        dy, dm, dd = row[2], row[3], row[4]
        old = dict(zip(REPARSE_COLS, row[5:5 + len(REPARSE_COLS)]))
        old_date_mismatch = row[5 + len(REPARSE_COLS)]

        name = path.rsplit("/", 1)[-1]
        new = parse_filename(name, sta)

        # Recompute date_mismatch
        if new["file_year"] is not None and dy is not None:
            new_date_mismatch = int(
                (new["file_year"], new["file_month"], new["file_day"])
                != (dy, dm, dd))
        else:
            new_date_mismatch = None

        # Did anything change?
        changed = False
        for c in REPARSE_COLS:
            if old[c] != new[c]:
                changed = True
                break
        if not changed and old_date_mismatch == new_date_mismatch:
            continue

        n_changed += 1
        if old["exclude_reason"] == "unparsed" and new["exclude_reason"] is None:
            n_unparsed_now_parsed += 1
        if old["exclude_reason"] and new["exclude_reason"] is None:
            n_was_excluded_now_clean += 1
        key = new["exclude_reason"] or "<none>"
        by_new_excl[key] = by_new_excl.get(key, 0) + 1

        update_batch.append((
            new["recorder_type"], new["source_type"], new["role"],
            new["file_year"], new["file_month"], new["file_day"],
            new["hhmm"], new["ss"], new["channel_suffix"],
            new["filename_station"], new["station_mismatch"],
            new["flags"], new["exclude_reason"],
            new_date_mismatch,
            path,
        ))

    if update_batch and not dry_run:
        # Batch the UPDATEs
        for i in range(0, len(update_batch), 10000):
            cur.executemany(UPDATE_SQL, update_batch[i:i + 10000])
        conn.commit()
    conn.close()

    return {
        "db": str(db_path),
        "n_total": n_total,
        "n_changed": n_changed,
        "n_unparsed_now_parsed": n_unparsed_now_parsed,
        "n_was_excluded_now_clean": n_was_excluded_now_clean,
        "by_new_excl": by_new_excl,
        "elapsed_s": round(time.time() - t0, 1),
        "dry_run": dry_run,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target",
                   help="Either a single DB path, or a directory holding "
                        "VW.<STA>.db files (use with --all-vw)")
    ap.add_argument("--all-vw", action="store_true",
                   help="Treat target as a dir; reparse every VW.*.db")
    ap.add_argument("--stations", nargs="*",
                   help="Restrict --all-vw to these station codes")
    ap.add_argument("--dry-run", action="store_true",
                   help="Compute deltas but don't UPDATE")
    args = ap.parse_args()

    target = Path(args.target)
    if args.all_vw:
        if not target.is_dir():
            print(f"ERROR: --all-vw expects a directory; got {target}", file=sys.stderr)
            return 2
        dbs = sorted(target.glob("VW.*.db"))
        if args.stations:
            wanted = set(args.stations)
            dbs = [d for d in dbs if d.stem.split(".")[1] in wanted]
    else:
        if not target.is_file():
            print(f"ERROR: DB not found: {target}", file=sys.stderr)
            return 2
        dbs = [target]

    total = {"n_changed": 0, "n_unparsed_now_parsed": 0, "n_was_excluded_now_clean": 0}
    for db in dbs:
        sta = db.stem.split(".")[-1] if "." in db.stem else db.stem
        print(f"\n--- {sta} ({db}) ---", flush=True)
        r = reparse_db(db, dry_run=args.dry_run)
        print(f"  rows: {r['n_total']:,}")
        print(f"  changed: {r['n_changed']:,}")
        print(f"  unparsed -> parsed: {r['n_unparsed_now_parsed']:,}")
        print(f"  was excluded -> now clean: {r['n_was_excluded_now_clean']:,}")
        if r["by_new_excl"]:
            print(f"  new exclude_reason distribution (among changed rows):")
            for k, v in sorted(r["by_new_excl"].items(), key=lambda x: -x[1]):
                print(f"    {k:30s} {v:>10,}")
        print(f"  elapsed: {r['elapsed_s']}s {'[DRY-RUN]' if r['dry_run'] else ''}")
        for k in total:
            total[k] += r[k]

    print(f"\n=== TOTAL across {len(dbs)} DB(s) ===")
    for k, v in total.items():
        print(f"  {k}: {v:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

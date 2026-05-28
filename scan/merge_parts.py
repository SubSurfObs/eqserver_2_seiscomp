#!/usr/bin/env python3
"""Merge already-scanned Level-1 part-DBs into a single manifest, deleting
each part as soon as it has been successfully merged.

Recovery utility for when the in-line merge in level1.py crashed (e.g. disk
full). The parts are still on disk; this script consumes them progressively
so the working-set never exceeds parts(remaining) + final.

Run:
  python3 scan/merge_parts.py /path/to/parts_dir /path/to/final.db
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
import time

# Schema WITHOUT a PRIMARY KEY constraint during the merge. The implicit PK
# index on `path` is the dominant cost of INSERT OR REPLACE on a growing
# table — every insert turns into an O(log n) disk-backed index lookup once
# the table exceeds page cache.
#
# Parts are guaranteed non-overlapping by construction (one part per
# (station, year), and the level-1 scanner only writes a row once per file
# path), so we can use plain INSERT INTO with no conflict resolution.
#
# At the end we create the indexes — INCLUDING a UNIQUE index on `path`,
# which would fail loudly if any cross-part path collision had snuck in.
SCHEMA_FAST = """
CREATE TABLE IF NOT EXISTS files (
    path            TEXT,
    station         TEXT,
    dir_year        INTEGER, dir_month INTEGER, dir_day INTEGER,
    recorder_type   TEXT, source_type TEXT, role TEXT,
    file_year       INTEGER, file_month INTEGER, file_day INTEGER,
    date_mismatch   INTEGER,
    hhmm            TEXT, ss TEXT, channel_suffix TEXT,
    filename_station TEXT, station_mismatch INTEGER,
    flags           TEXT,
    size_bytes      INTEGER, mtime REAL, exclude_reason TEXT
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parts_dir")
    ap.add_argument("final_db")
    ap.add_argument("--keep-parts", action="store_true",
                    help="don't delete part-DB after successful merge "
                         "(default: delete, frees disk progressively)")
    ap.add_argument("--no-indexes", action="store_true",
                    help="skip building indexes at the end (caller will do it)")
    args = ap.parse_args()

    if os.path.exists(args.final_db):
        print(f"ERROR: {args.final_db} already exists; remove it first")
        return 2

    part_paths = sorted(
        os.path.join(args.parts_dir, f)
        for f in os.listdir(args.parts_dir)
        if f.endswith(".sqlite")
    )
    print(f"[merge] {len(part_paths)} part-DBs in {args.parts_dir}", flush=True)

    conn = sqlite3.connect(args.final_db)
    conn.executescript(SCHEMA_FAST)
    # Aggressive PRAGMAs: no journal at all (crash means corrupt final DB,
    # which is fine because we restart from the parts). Large page cache so
    # most writes hit memory before flushing.
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -524288")   # 512 MB page cache
    conn.execute("PRAGMA locking_mode = EXCLUSIVE")
    # Single mega-transaction for the entire merge. Avoids per-part commit
    # overhead entirely. SQLite holds everything in journal-less WAL=OFF
    # state, only durable when we COMMIT at the end.
    conn.execute("BEGIN")

    # Number of columns from the schema (must match SCHEMA_FAST).
    n_cols = 21
    placeholders = ",".join(["?"] * n_cols)
    insert_sql = f"INSERT INTO files VALUES ({placeholders})"
    BATCH = 50_000

    t0 = time.time()
    rows_total = 0
    last_emit = t0
    for i, p in enumerate(part_paths, 1):
        try:
            # Fresh per-part connection. Lighter than ATTACH; closes cleanly.
            pc = sqlite3.connect(p)
            cur = pc.execute("SELECT * FROM files")
            n = 0
            while True:
                batch = cur.fetchmany(BATCH)
                if not batch:
                    break
                conn.executemany(insert_sql, batch)
                n += len(batch)
            pc.close()
            rows_total += n
        except Exception as e:
            print(f"[merge] FAIL on {os.path.basename(p)}: {e}", flush=True)
            conn.rollback()
            return 1
        if not args.keep_parts:
            os.remove(p)
        # Time-based emit: every 30s or every 25 parts, whichever comes first.
        now = time.time()
        if (i % 25 == 0) or (i == len(part_paths)) or (now - last_emit > 30):
            elapsed = now - t0
            rate = rows_total / elapsed if elapsed > 0 else 0
            db_mb = os.path.getsize(args.final_db) / 1e6
            print(f"  [{i:>4}/{len(part_paths)}] rows={rows_total:>12,} "
                  f"db={db_mb:>7.0f}MB elapsed={elapsed:>6.0f}s rate={rate:>8,.0f} rows/s",
                  flush=True)
            last_emit = now

    # Close the mega-transaction. This is where the final durability
    # happens; until now all inserts have been in-memory + journal=off.
    print(f"[merge] committing mega-transaction ({rows_total:,} rows)...", flush=True)
    tc = time.time()
    conn.commit()
    print(f"[merge] commit done in {time.time()-tc:.1f}s", flush=True)

    if not args.no_indexes:
        # Build PRIMARY-KEY-equivalent UNIQUE index FIRST. If any two parts
        # had the same path (shouldn't, but verify), this will fail loudly
        # so the bad state surfaces immediately rather than going silent.
        print(f"[merge] building indexes (UNIQUE path first to verify no dup)...", flush=True)
        ti = time.time()
        try:
            conn.execute("CREATE UNIQUE INDEX ix_path_unique ON files(path)")
        except sqlite3.IntegrityError as e:
            print(f"[merge] FAIL: duplicate paths across parts — {e}", flush=True)
            print("[merge] Investigate with: SELECT path, COUNT(*) c FROM files "
                  "GROUP BY path HAVING c > 1 LIMIT 20", flush=True)
            return 2
        print(f"  ix_path_unique done in {time.time()-ti:.1f}s", flush=True)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_station ON files(station)",
            "CREATE INDEX IF NOT EXISTS ix_recorder ON files(recorder_type)",
            "CREATE INDEX IF NOT EXISTS ix_role ON files(role)",
            "CREATE INDEX IF NOT EXISTS ix_station_dir ON files(station, dir_year, dir_month, dir_day)",
            "CREATE INDEX IF NOT EXISTS ix_station_role ON files(station, role)",
        ]:
            tj = time.time()
            conn.execute(idx_sql)
            conn.commit()
            print(f"  {idx_sql.split('IF NOT EXISTS ')[1].split(' ON')[0]:25s} done in {time.time()-tj:.1f}s", flush=True)
        print(f"[merge] all indexes done in {time.time()-ti:.1f}s total", flush=True)
    conn.close()

    print(f"\n[merge] DONE. {rows_total:,} rows, final db: "
          f"{os.path.getsize(args.final_db) / 1e9:.1f} GB, "
          f"{time.time()-t0:.0f}s total", flush=True)


if __name__ == "__main__":
    sys.exit(main())

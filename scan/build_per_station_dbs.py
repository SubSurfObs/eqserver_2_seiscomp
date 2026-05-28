#!/usr/bin/env python3
"""Build per-station manifest DBs from level-1 part-DBs.

Each part is named `<STA>__<YEAR>.sqlite`. This script groups parts by station,
then for each station concatenates its year-parts into a single small DB
`<NET>.<STA>.db` (network looked up from the station registry).

Compared with merging into one monolithic manifest, per-station DBs:
  - Avoid the disk-IO wall (~24k rows/s ceiling) of growing a 100M-row table
  - Match the Phase 3 per-station processing unit naturally
  - Can be rebuilt for ONE station without re-merging the rest
  - Are small (~300-700 MB each) so all queries are fast and in-memory-friendly

Per-station merging is fast because each station's table is small enough that
the implicit PK index lookup stays cheap — the global merger only slowed down
because the table grew past page cache.

Run:
  python3 scan/build_per_station_dbs.py <parts_dir> <out_dir> --registry metadata/station_registry.yaml [--workers N]
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

# Schema WITHOUT PRIMARY KEY constraint during the build. The implicit PK
# index on `path` is the dominant cost of INSERT OR REPLACE on a growing
# table — every insert turns into an O(log n) disk-backed index lookup once
# the table exceeds page cache.
#
# Parts are non-overlapping by construction (one per (station, year), each
# row's path is unique), so plain INSERT INTO is safe.
#
# Indexes (including UNIQUE on path) are added at the end — UNIQUE catches
# any cross-part duplicate that snuck in as a hard error.
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

POST_INDEXES = [
    "CREATE UNIQUE INDEX ix_path_unique ON files(path)",
    "CREATE INDEX IF NOT EXISTS ix_station ON files(station)",
    "CREATE INDEX IF NOT EXISTS ix_recorder ON files(recorder_type)",
    "CREATE INDEX IF NOT EXISTS ix_role ON files(role)",
    "CREATE INDEX IF NOT EXISTS ix_station_dir ON files(station, dir_year, dir_month, dir_day)",
    "CREATE INDEX IF NOT EXISTS ix_station_role ON files(station, role)",
]


def build_one_station(args):
    """Worker: build one per-station DB from its part files.
    Returns (station, n_rows, db_size_mb, elapsed_s, n_parts)."""
    station, parts, net, out_dir, delete_parts = args
    out_path = os.path.join(out_dir, f"{net}.{station}.db")
    if os.path.exists(out_path):
        os.remove(out_path)

    t0 = time.time()
    conn = sqlite3.connect(out_path)
    conn.executescript(SCHEMA_FAST)
    # Aggressive PRAGMAs: same pattern as the global v3 merger that finally
    # achieved decent throughput. Per-station DB is small enough that the
    # one big transaction fits comfortably in page cache.
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -131072")   # 128 MB per worker
    conn.execute("PRAGMA locking_mode = EXCLUSIVE")
    conn.execute("BEGIN")

    n_cols = 21
    placeholders = ",".join(["?"] * n_cols)
    insert_sql = f"INSERT INTO files VALUES ({placeholders})"
    BATCH = 50_000

    n_rows = 0
    for p in parts:
        # Fresh per-part read connection — lighter than ATTACH.
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
        n_rows += n
        if delete_parts:
            os.remove(p)

    conn.commit()
    for idx_sql in POST_INDEXES:
        conn.execute(idx_sql)
    conn.commit()
    conn.close()
    sz = os.path.getsize(out_path) / 1e6
    return station, n_rows, sz, time.time() - t0, len(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parts_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--registry", required=True,
                    help="path to metadata/station_registry.yaml (for network lookup)")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel station-build workers (default 4)")
    ap.add_argument("--keep-parts", action="store_true",
                    help="don't delete part-DBs after building station DBs (default: delete)")
    ap.add_argument("--stations", default="",
                    help="comma-separated subset (default: all stations with parts)")
    args = ap.parse_args()

    import yaml
    reg = yaml.safe_load(open(args.registry))
    station_net = {s: v.get("target_network")
                   for s, v in reg.items()
                   if isinstance(v, dict) and v.get("include") and v.get("target_network")}

    # Group parts by station from filename.
    by_station = defaultdict(list)
    for f in sorted(os.listdir(args.parts_dir)):
        if not f.endswith(".sqlite"):
            continue
        sta = f.split("__")[0]
        by_station[sta].append(os.path.join(args.parts_dir, f))

    if args.stations:
        wanted = {s.strip() for s in args.stations.split(",") if s.strip()}
        by_station = {s: p for s, p in by_station.items() if s in wanted}

    os.makedirs(args.out_dir, exist_ok=True)
    print(f"[per-station] {len(by_station)} stations with parts in {args.parts_dir}", flush=True)

    jobs = []
    for sta, parts in sorted(by_station.items()):
        net = station_net.get(sta, "UNK")
        jobs.append((sta, parts, net, args.out_dir, not args.keep_parts))

    t0 = time.time()
    done = 0
    total_rows = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(build_one_station, j): j[0] for j in jobs}
        for fut in as_completed(futures):
            try:
                sta, n, sz, el, np_ = fut.result()
                done += 1
                total_rows += n
                print(f"  [{done:>3}/{len(jobs)}] {sta:8s} parts={np_:>2} "
                      f"rows={n:>10,} db={sz:>6.0f}MB time={el:>5.1f}s", flush=True)
            except Exception as e:
                sta = futures[fut]
                print(f"  FAIL {sta}: {type(e).__name__}: {e}", flush=True)

    print(f"\n[per-station] done in {time.time()-t0:.1f}s")
    print(f"  total rows across all per-station DBs: {total_rows:,}")
    print(f"  output dir: {args.out_dir}")


if __name__ == "__main__":
    sys.exit(main())

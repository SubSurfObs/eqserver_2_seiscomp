#!/usr/bin/env python3
"""test_env_build.py — manage the Class B test environment.

The test env lives at /mnt/seiscomp_staging/test_env_classb/ and is a
segregated, miniature mirror of the EqServer archive for a small set of
representative days. Each test day is a complete day-directory rsync'd
byte-identical from the read-only EqServer NFS. Production pipeline code
can then be run against the mirror without touching production paths.

Layout:
  /mnt/seiscomp_staging/test_env_classb/
  ├── mirror_src/<STA>/continuous/<YYYY>/<MM>/<DD>/   rsync'd source
  ├── manifest_dbs/VW.<STA>.db                         mini Level-1 DBs
  ├── time_index/<STA>.time_index.db                   per-trace time ranges
  ├── plans/VW.<STA>.plan.yaml                         copied from prod plans
  ├── staging_sds/                                     phase3 output
  ├── queue/                                           orchestrator state
  ├── logs/                                            convert/verify logs
  └── catalogue.yaml                                   registry of test days

Subcommands:
  add STA YYYY-MM-DD CATEGORY [--notes "..."]
      rsync the day directory from EqServer NFS, register in catalogue,
      build/update the mini manifest DB and time-index DB for that day.

  list
      print the catalogue with current state (mirrored / db built / etc).

  rebuild_time_index STA YYYY-MM-DD
      re-derive time index for that day from the mirrored bytes
      (useful if the time-index code changes).

  verify_mirror STA YYYY-MM-DD
      check the mirror is byte-identical to source via stat counts and
      sample md5sum on a few files. Cheap.

USAGE:
  python3 scan/test_env_build.py add STBK 2022-10-23 true_b2_canonical \\
      --notes "disk_to_sds canonical test day"
  python3 scan/test_env_build.py list
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import warnings
import zipfile
from datetime import datetime
from pathlib import Path

import yaml

# Local-disk root: SQLite + yaml live here because CIFS locking is broken.
# This matches production: station_dbs are on local disk for the same reason.
TEST_ENV_LOCAL = Path("/home/unimelb.edu.au/dsand/test_env_classb")
MIRROR_SRC = TEST_ENV_LOCAL / "mirror_src"
MANIFEST_DBS = TEST_ENV_LOCAL / "manifest_dbs"
TIME_INDEX = TEST_ENV_LOCAL / "time_index"
PLANS = TEST_ENV_LOCAL / "plans"
LOGS = TEST_ENV_LOCAL / "logs"
QUEUE = TEST_ENV_LOCAL / "queue"
CATALOGUE = TEST_ENV_LOCAL / "catalogue.yaml"

# Shared-mount root: only used for staging_sds output (so dev1 can read for
# apply.py dry-runs). Created on-demand by the run step, not by add.
TEST_ENV_SHARED = Path("/mnt/seiscomp_staging/test_env_classb")
STAGING_SDS = TEST_ENV_SHARED / "staging_sds"

EQSERVER_ROOT = Path("/mnt/eqserver_archive/shared/data/repository/archive")
PROD_PLANS = Path("/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp/plans/VW")


# --------------------------------------------------------------------------
# Catalogue: yaml-backed registry of test days
# --------------------------------------------------------------------------

def load_catalogue() -> list[dict]:
    if not CATALOGUE.exists():
        return []
    return yaml.safe_load(CATALOGUE.read_text()) or []


def save_catalogue(entries: list[dict]) -> None:
    CATALOGUE.write_text(yaml.safe_dump(entries, sort_keys=False, indent=2))


def catalogue_key(sta: str, year: int, month: int, day: int) -> str:
    return f"{sta}_{year:04d}-{month:02d}-{day:02d}"


# --------------------------------------------------------------------------
# Mirror: rsync the day directory from EqServer NFS
# --------------------------------------------------------------------------

def mirror_day(sta: str, year: int, month: int, day: int) -> dict:
    """rsync the day from EqServer NFS into mirror_src. Returns {n_files, bytes}."""
    src = EQSERVER_ROOT / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    if not src.exists():
        return {"error": f"source not found: {src}"}
    dst = MIRROR_SRC / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    dst.mkdir(parents=True, exist_ok=True)
    # rsync -a preserves perms, times, symlinks. --checksum is overkill but
    # uses md5 to verify content match rather than mtime+size.
    cmd = ["rsync", "-a", str(src) + "/", str(dst) + "/"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"error": f"rsync failed: {proc.stderr}"}
    files = list(dst.iterdir())
    total = sum(f.stat().st_size for f in files if f.is_file())
    return {"n_files": len(files), "bytes": total}


def verify_mirror(sta: str, year: int, month: int, day: int) -> dict:
    """Check mirror == source by file count + sampled md5sum."""
    src = EQSERVER_ROOT / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    dst = MIRROR_SRC / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    src_files = sorted(src.iterdir())
    dst_files = sorted(dst.iterdir())
    src_names = {f.name for f in src_files}
    dst_names = {f.name for f in dst_files}
    missing = src_names - dst_names
    extra = dst_names - src_names
    if missing or extra:
        return {"ok": False, "missing": list(missing)[:5], "extra": list(extra)[:5]}
    # md5 a small sample (3 files)
    import hashlib
    sample = sorted(src_names)[:3]
    mismatches = []
    for name in sample:
        h_src = hashlib.md5((src / name).read_bytes()).hexdigest()
        h_dst = hashlib.md5((dst / name).read_bytes()).hexdigest()
        if h_src != h_dst:
            mismatches.append(name)
    return {"ok": not mismatches, "n_files": len(src_files), "checked": sample,
            "mismatches": mismatches}


# --------------------------------------------------------------------------
# Mini manifest DB — same schema as production Level-1 DBs.
# We don't fully recreate Level-1 here; we copy rows from prod station_db
# for the (station, year, month, day) we mirrored. That guarantees schema
# compatibility with phase3 and avoids re-implementing the classifier.
# --------------------------------------------------------------------------

PROD_DBS = Path("/home/unimelb.edu.au/dsand/station_dbs")


def build_mini_db(sta: str, year: int, month: int, day: int) -> dict:
    """Build/update mini manifest DB by copying rows from prod station DB.

    We also rewrite the `path` column to point at mirror_src instead of the
    EqServer NFS source — phase3 will read FROM the mirror, not the original.
    """
    prod_db = PROD_DBS / f"VW.{sta}.db"
    if not prod_db.exists():
        return {"error": f"prod DB not found: {prod_db}"}
    mini_db = MANIFEST_DBS / f"VW.{sta}.db"

    # 1. If mini DB doesn't exist, copy the schema (no rows) from prod
    if not mini_db.exists():
        # Open prod, dump schema only
        src_conn = sqlite3.connect(prod_db)
        schema_rows = src_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table', 'index') "
            "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
        src_conn.close()
        dst_conn = sqlite3.connect(mini_db)
        for (sql,) in schema_rows:
            dst_conn.execute(sql)
        dst_conn.commit()
        dst_conn.close()

    # 2. Copy rows for this (sta, year, month, day) from prod, rewriting path
    src_conn = sqlite3.connect(prod_db)
    rows = src_conn.execute(
        "SELECT * FROM files WHERE station = ? AND dir_year = ? "
        "AND dir_month = ? AND dir_day = ?",
        (sta, year, month, day)
    ).fetchall()
    cols = [d[0] for d in src_conn.execute("SELECT * FROM files LIMIT 1").description]
    src_conn.close()
    if not rows:
        return {"error": f"no rows in prod DB for {sta} {year}-{month:02d}-{day:02d}"}

    path_idx = cols.index("path")
    rewritten = []
    for r in rows:
        r = list(r)
        # Original path: /mnt/eqserver_archive/.../STA/continuous/YYYY/MM/DD/<file>
        # Rewrite to:    /mnt/seiscomp_staging/test_env_classb/mirror_src/STA/continuous/YYYY/MM/DD/<file>
        if r[path_idx] and "/mnt/eqserver_archive/" in r[path_idx]:
            fname = Path(r[path_idx]).name
            new_path = str(MIRROR_SRC / sta / "continuous" / f"{year:04d}" /
                           f"{month:02d}" / f"{day:02d}" / fname)
            r[path_idx] = new_path
        rewritten.append(tuple(r))

    dst_conn = sqlite3.connect(mini_db)
    # Delete any existing rows for this day first (idempotent)
    dst_conn.execute(
        "DELETE FROM files WHERE station = ? AND dir_year = ? "
        "AND dir_month = ? AND dir_day = ?",
        (sta, year, month, day)
    )
    ph = ",".join("?" * len(cols))
    dst_conn.executemany(f"INSERT INTO files VALUES ({ph})", rewritten)
    dst_conn.commit()
    dst_conn.close()
    return {"n_rows": len(rewritten)}


# --------------------------------------------------------------------------
# Time-index DB — records what time-range each source file actually contains.
# Distinct from manifest (which records filename metadata).
# --------------------------------------------------------------------------

TIME_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS time_index (
    sta              TEXT NOT NULL,
    chan             TEXT NOT NULL,
    source_kind      TEXT NOT NULL,
    source_path      TEXT NOT NULL,
    trace_start_iso  TEXT NOT NULL,
    trace_end_iso    TEXT NOT NULL,
    npts             INTEGER NOT NULL,
    sample_rate      REAL NOT NULL,
    PRIMARY KEY (source_path, chan, trace_start_iso)
);
CREATE INDEX IF NOT EXISTS ix_range
ON time_index(sta, chan, trace_start_iso, trace_end_iso);
"""


def _detect_source_kind(name: str) -> str:
    """Return disk | tele_ss00 | tele_noss | unknown based on filename grammar."""
    import re
    if re.match(r"^\d{8}_\d{4}_\w+\.ms(\.zip)?$", name):
        return "disk"
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.ms(\.zip)?$", name):
        return "tele_ss00"
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ms(\.zip)?$", name):
        return "tele_noss"
    return "unknown"


def build_time_index(sta: str, year: int, month: int, day: int) -> dict:
    """Read each mirrored mseed file and record per-trace time ranges."""
    warnings.filterwarnings("ignore")
    from obspy import read

    day_dir = MIRROR_SRC / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    if not day_dir.exists():
        return {"error": f"mirror dir not found: {day_dir}"}

    db_path = TIME_INDEX / f"{sta}.time_index.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(TIME_INDEX_SCHEMA)

    # Clear any existing rows for this day (idempotent)
    iso_day_start = f"{year:04d}-{month:02d}-{day:02d}T00:00:00Z"
    iso_day_end = f"{year:04d}-{month:02d}-{day:02d}T23:59:59.999Z"
    conn.execute(
        "DELETE FROM time_index WHERE sta=? AND source_path LIKE ?",
        (sta, str(day_dir) + "/%")
    )

    n_rows = 0
    n_read_errors = 0
    for f in sorted(day_dir.iterdir()):
        if not f.is_file():
            continue
        name = f.name
        kind = _detect_source_kind(name)
        if kind == "unknown":
            continue
        try:
            if name.endswith(".zip"):
                with zipfile.ZipFile(f) as zf:
                    for member in zf.namelist():
                        if not member.endswith((".ms", ".mseed")):
                            continue
                        st = read(io.BytesIO(zf.read(member)),
                                  format="MSEED", headonly=True)
                        for tr in st:
                            conn.execute(
                                "INSERT OR REPLACE INTO time_index VALUES "
                                "(?, ?, ?, ?, ?, ?, ?, ?)",
                                (sta, tr.stats.channel, kind, str(f),
                                 str(tr.stats.starttime).rstrip("Z") + "Z",
                                 str(tr.stats.endtime).rstrip("Z") + "Z",
                                 tr.stats.npts, tr.stats.sampling_rate)
                            )
                            n_rows += 1
            else:
                st = read(str(f), format="MSEED", headonly=True)
                for tr in st:
                    conn.execute(
                        "INSERT OR REPLACE INTO time_index VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?)",
                        (sta, tr.stats.channel, kind, str(f),
                         str(tr.stats.starttime).rstrip("Z") + "Z",
                         str(tr.stats.endtime).rstrip("Z") + "Z",
                         tr.stats.npts, tr.stats.sampling_rate)
                    )
                    n_rows += 1
        except Exception as e:
            n_read_errors += 1
            continue
    conn.commit()
    conn.close()
    return {"n_rows": n_rows, "read_errors": n_read_errors}


# --------------------------------------------------------------------------
# Plans: borrow from prod
# --------------------------------------------------------------------------

def copy_plan(sta: str) -> dict:
    src = PROD_PLANS / f"VW.{sta}.plan.yaml"
    if not src.exists():
        return {"error": f"prod plan not found: {src}"}
    dst = PLANS / f"VW.{sta}.plan.yaml"
    dst.write_bytes(src.read_bytes())
    return {"ok": True, "src": str(src), "dst": str(dst)}


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_add(args):
    sta = args.sta
    year, month, day = (int(p) for p in args.date.split("-"))
    print(f"[add] {sta} {year}-{month:02d}-{day:02d}  category={args.category}",
          flush=True)

    print("  [mirror]   rsync ...", flush=True)
    r = mirror_day(sta, year, month, day)
    print(f"  [mirror]   {r}", flush=True)
    if "error" in r:
        return 2

    print("  [mini_db]  copy rows from prod ...", flush=True)
    r2 = build_mini_db(sta, year, month, day)
    print(f"  [mini_db]  {r2}", flush=True)

    print("  [time_idx] read trace headers ...", flush=True)
    r3 = build_time_index(sta, year, month, day)
    print(f"  [time_idx] {r3}", flush=True)

    print("  [plan]     copy from prod ...", flush=True)
    r4 = copy_plan(sta)
    print(f"  [plan]     {r4}", flush=True)

    # register in catalogue (idempotent: replace if exists)
    entries = load_catalogue()
    key = catalogue_key(sta, year, month, day)
    entries = [e for e in entries if catalogue_key(
        e["sta"], e["year"], e["month"], e["day"]) != key]
    entries.append({
        "sta": sta, "year": year, "month": month, "day": day,
        "category": args.category,
        "notes": args.notes or "",
        "added": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mirror_files": r.get("n_files"),
        "mirror_bytes": r.get("bytes"),
        "manifest_rows": r2.get("n_rows"),
        "time_index_rows": r3.get("n_rows"),
    })
    save_catalogue(entries)
    print(f"  [catalog]  registered: {key}", flush=True)
    return 0


def cmd_list(args):
    entries = load_catalogue()
    if not entries:
        print("[list] catalogue is empty", flush=True)
        return 0
    print(f"[list] {len(entries)} days registered")
    print(f"{'sta':8s}  {'date':12s}  {'category':22s}  files  m_rows  t_rows  notes")
    print("-" * 100)
    for e in sorted(entries, key=lambda x: (x["sta"], x["year"], x["month"], x["day"])):
        print(f"{e['sta']:8s}  {e['year']}-{e['month']:02d}-{e['day']:02d}    "
              f"{e['category']:22s}  {e.get('mirror_files') or '—':5}  "
              f"{e.get('manifest_rows') or '—':6}  "
              f"{e.get('time_index_rows') or '—':6}  {e.get('notes', '')[:30]}")
    return 0


def cmd_verify_mirror(args):
    year, month, day = (int(p) for p in args.date.split("-"))
    r = verify_mirror(args.sta, year, month, day)
    print(r)
    return 0 if r.get("ok") else 1


def cmd_rebuild_time_index(args):
    year, month, day = (int(p) for p in args.date.split("-"))
    r = build_time_index(args.sta, year, month, day)
    print(r)
    return 0 if "error" not in r else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="add a day to the test env")
    p.add_argument("sta")
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("category", help="e.g. true_b2_canonical, clean_baseline, b1_isolation")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="show registered test days")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("verify_mirror", help="check mirror == source")
    p.add_argument("sta")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_verify_mirror)

    p = sub.add_parser("rebuild_time_index", help="re-read mirror, refresh time index")
    p.add_argument("sta")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_rebuild_time_index)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

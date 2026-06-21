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
REPO = Path("/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp")
LEVEL1 = REPO / "scan" / "level1.py"
PLAN_GENERATOR = REPO / "scan" / "plan_generator.py"
REGISTRY = REPO / "metadata" / "station_registry.yaml"
PYTHON = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"


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
# Manifest DB — REBUILT FROM SCRATCH by invoking scan/level1.py against the
# mirror. We do NOT copy from prod station_dbs. Reason: if a fix touches
# Level-1 scan logic (classifier, source-type tagging, ss-field handling),
# the test env must exercise it. Copying prod artifacts would test stale
# state.
# --------------------------------------------------------------------------

def run_level1(sta: str) -> dict:
    """Invoke scan/level1.py against mirror_src for one station.
    Writes the per-station DB to manifest_dbs/VW.<STA>.db. Scans the WHOLE
    mirror tree for that station, so if multiple days are mirrored, all
    are indexed in the same DB."""
    # level1.py expects the archive root to look like archive/<STA>/continuous/...
    # The mirror already has that shape. Just pass it.
    # level1.py also wants a `--db` for the global manifest. We pass a
    # throwaway under logs/ since we only care about the per-station DB.
    throwaway_db = LOGS / f"level1_throwaway_{sta}.db"
    if throwaway_db.exists():
        throwaway_db.unlink()
    cmd = [PYTHON, "-u", str(LEVEL1),
           "--archive", str(MIRROR_SRC),
           "--db", str(throwaway_db),
           "--per-station-dbs", str(MANIFEST_DBS),
           "--stations", sta,
           "--registry", str(REGISTRY)]
    log_path = LOGS / f"level1_{sta}.log"
    with log_path.open("w") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        return {"error": f"level1 failed rc={proc.returncode}; see {log_path}"}
    # Count rows in the produced per-station DB
    db_path = MANIFEST_DBS / f"VW.{sta}.db"
    if not db_path.exists():
        return {"error": f"level1 produced no DB at {db_path}"}
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    conn.close()
    return {"n_rows": n, "log_path": str(log_path)}


def run_plan_generator(sta: str) -> dict:
    """Invoke scan/plan_generator.py against the rebuilt manifest DB.
    Writes plan to plans/VW.<STA>.plan.yaml.
    The plan generator reads cross_source decisions implicitly via the
    classifier — so any change to check_manifest.py or cross_source.py
    surfaces here."""
    db_path = MANIFEST_DBS / f"VW.{sta}.db"
    if not db_path.exists():
        return {"error": f"manifest DB missing — run level1 first: {db_path}"}
    cmd = [PYTHON, "-u", str(PLAN_GENERATOR), str(db_path),
           "--registry", str(REGISTRY),
           "--out", str(PLANS),  # plan_generator writes to <out>/VW.<STA>.plan.yaml flat
           "--stations", sta]
    log_path = LOGS / f"plan_generator_{sta}.log"
    with log_path.open("w") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        return {"error": f"plan_generator failed rc={proc.returncode}; see {log_path}"}
    plan_path = PLANS / f"VW.{sta}.plan.yaml"
    if not plan_path.exists():
        return {"error": f"plan_generator produced no output for {sta}; see {log_path}"}
    return {"ok": True, "plan_path": str(plan_path)}


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


def _detect_source_kind(name: str) -> tuple[str, str]:
    """Return (source_kind, format) — source_kind in
    {disk_mseed, disk_suds, tele_ss_mseed, tele_noss_mseed, tele_ss_suds,
     tele_noss_suds, unknown}, format in {mseed, suds, unknown}.

    Disk grammar: underscore-separated, no spaces.
      - EchoPro disk: YYYY-MM-DD_HHMM_SS_STA.dmx[.gz]   (dashed date! 3 underscores)
      - Gecko   disk: YYYYMMDD_HHMM_STA.ms.zip           (numeric date, 2 underscores)

    Telemetry grammar: space-separated, dashed date.
      - EchoPro tele: 'YYYY-MM-DD HHMM SS STA.dmx[.gz]'  (has SS)
                      'YYYY-MM-DD HHMM STA.dmx[.gz]'     (no SS)
      - Gecko   tele: 'YYYY-MM-DD HHMM SS STA.ms.zip'    (has SS)
                      'YYYY-MM-DD HHMM STA.ms.zip'       (no SS)

    Triggered EchoPro: '... STA.N.dmx' where N is event-index. Skip — these
    are accelerometer triggers, not continuous waveform.
    """
    import re
    # Triggered EchoPro — exclude. Patterns observed:
    #   STA.N.dmx[.gz]          (numbered triggered)
    #   STA.trig.dmx[.gz]       (explicit triggered marker)
    #   STA.N.trig.dmx[.gz]     (numbered explicit triggered)
    if re.match(r"^.+\.(trig|\d+(\.trig)?)\.dmx(\.gz)?$", name):
        return ("triggered_suds", "unknown")
    # SUDS — EchoPro
    if re.match(r"^\d{4}-\d{2}-\d{2}_\d{4}_\d{2}_\w+\.dmx(\.gz)?$", name):
        return ("disk_suds", "suds")
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.dmx(\.gz)?$", name):
        return ("tele_ss_suds", "suds")
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.dmx(\.gz)?$", name):
        return ("tele_noss_suds", "suds")
    # MSEED — Gecko-family (gecko, RT130-via-gecko, minimus per-chan)
    if re.match(r"^\d{8}_\d{4}_\w+\.ms\.zip$", name):
        return ("disk_mseed", "mseed")
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.ms\.zip$", name):
        return ("tele_ss_mseed", "mseed")
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ms\.zip$", name):
        return ("tele_noss_mseed", "mseed")
    # Mixed-separator shapes (surfaced 2026-06-22; ~1.73M files network-wide
    # at SGWU/TRPU/LOYU/DDWB/WPSH). Mirrors MIXED_SHAPES in scan/level1.py.
    # All three carry full 3-channel mseed; we group under one kind here
    # and let downstream branch on the flags column.
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{4}_\w+\.ms\.zip$", name):       # underscore_tele
        return ("tele_mixed_mseed", "mseed")
    if re.match(r"^\d{4}-\d{2}-\d{2}_\d{4}-\w+\.ms\.zip$", name):       # dash_around_sta
        return ("tele_mixed_mseed", "mseed")
    if re.match(r"^\d{4}-\d{2}-\d{2}-\d{4}_\w+\.ms\.zip$", name):       # all_dash_date
        return ("tele_mixed_mseed", "mseed")
    # Per-channel mseed stubs (gecko/minimus) — single-channel, in .mseed.zip
    if re.match(r"^.+\.mseed(\.zip)?$", name):
        return ("perchan_mseed", "mseed")
    return ("unknown", "unknown")


def _index_mseed(path: Path, sta: str, kind: str, conn) -> int:
    """Read mseed bytes (raw or zipped) and insert time-index rows.
    Returns count of rows inserted."""
    from obspy import read
    n = 0
    if str(path).endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                if not member.endswith((".ms", ".mseed")):
                    continue
                st = read(io.BytesIO(zf.read(member)),
                          format="MSEED", headonly=True)
                for tr in st:
                    conn.execute(
                        "INSERT OR REPLACE INTO time_index VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?)",
                        (sta, tr.stats.channel, kind, str(path),
                         str(tr.stats.starttime).rstrip("Z") + "Z",
                         str(tr.stats.endtime).rstrip("Z") + "Z",
                         tr.stats.npts, tr.stats.sampling_rate))
                    n += 1
    else:
        st = read(str(path), format="MSEED", headonly=True)
        for tr in st:
            conn.execute(
                "INSERT OR REPLACE INTO time_index VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?)",
                (sta, tr.stats.channel, kind, str(path),
                 str(tr.stats.starttime).rstrip("Z") + "Z",
                 str(tr.stats.endtime).rstrip("Z") + "Z",
                 tr.stats.npts, tr.stats.sampling_rate))
            n += 1
    return n


def _index_suds(path: Path, sta: str, kind: str, conn) -> int:
    """Use sudspy.scan_suds_file to read SUDS headers (no data decode) and
    insert time-index rows. Returns List[Dict] with keys:
    channel, start_time, end_time, npts, sample_rate.

    Channel is the SUDS-native form 'NET.STA.cNN' (e.g. 'AB.HOLS.c02'). We
    store just the component (c01/c02/c03) to keep the time-index uniform
    with the mseed branch which stores plain channel names (CHZ/CHN/CHE).
    The original channel can be recovered by joining with the manifest
    `channel_suffix` column if needed.
    """
    import sudspy
    n = 0
    entries = sudspy.scan_suds_file(str(path))
    for ch in entries:
        chan_full = ch.get("channel", "")
        # Extract trailing component if 'NET.STA.cNN' shape; else use as-is.
        chan = chan_full.split(".")[-1] if "." in chan_full else chan_full
        start = str(ch.get("start_time", ""))
        end = str(ch.get("end_time", ""))
        npts = int(ch.get("npts") or 0)
        rate = float(ch.get("sample_rate") or 0)
        conn.execute(
            "INSERT OR REPLACE INTO time_index VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (sta, chan, kind, str(path),
             start.rstrip("Z") + "Z" if start else "",
             end.rstrip("Z") + "Z" if end else "",
             npts, rate))
        n += 1
    return n


def build_time_index(sta: str, year: int, month: int, day: int) -> dict:
    """Read each mirrored source file and record per-trace time ranges.
    Handles SUDS (EchoPro) and MSEED (Gecko/Minimus/RT130) formats."""
    warnings.filterwarnings("ignore")

    day_dir = MIRROR_SRC / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    if not day_dir.exists():
        return {"error": f"mirror dir not found: {day_dir}"}

    db_path = TIME_INDEX / f"{sta}.time_index.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(TIME_INDEX_SCHEMA)

    # Clear any existing rows for this day's mirror path (idempotent)
    conn.execute(
        "DELETE FROM time_index WHERE sta=? AND source_path LIKE ?",
        (sta, str(day_dir) + "/%"))

    n_rows = 0
    n_read_errors = 0
    n_skipped_unknown = 0
    by_kind: dict = {}
    for f in sorted(day_dir.iterdir()):
        if not f.is_file():
            continue
        kind, fmt = _detect_source_kind(f.name)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if fmt == "unknown":
            n_skipped_unknown += 1
            continue
        try:
            if fmt == "mseed":
                n_rows += _index_mseed(f, sta, kind, conn)
            elif fmt == "suds":
                n_rows += _index_suds(f, sta, kind, conn)
        except Exception:
            n_read_errors += 1
            continue
    conn.commit()
    conn.close()
    return {"n_rows": n_rows, "read_errors": n_read_errors,
            "skipped_unknown": n_skipped_unknown, "files_by_kind": by_kind}


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

    print("  [level1]   run scan/level1.py against mirror ...", flush=True)
    r2 = run_level1(sta)
    print(f"  [level1]   {r2}", flush=True)
    if "error" in r2:
        return 2

    print("  [plan]     run scan/plan_generator.py against rebuilt manifest ...", flush=True)
    r4 = run_plan_generator(sta)
    print(f"  [plan]     {r4}", flush=True)
    if "error" in r4:
        return 2

    if args.no_time_index:
        print("  [time_idx] skipped (--no-time-index)", flush=True)
        r3 = {"n_rows": None}
    else:
        print("  [time_idx] read trace headers from mirror ...", flush=True)
        r3 = build_time_index(sta, year, month, day)
        print(f"  [time_idx] {r3}", flush=True)

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


def cmd_rebuild(args):
    """Re-run level1 + plan_generator for a station, against the existing
    mirror. Use after fixing scanner / classifier / plan-generator code to
    re-test against the same mirrored source bytes."""
    sta = args.sta
    print(f"[rebuild] {sta}  (mirror unchanged; re-running level1 + plan_generator)",
          flush=True)
    print("  [level1]   ...", flush=True)
    r2 = run_level1(sta)
    print(f"  [level1]   {r2}", flush=True)
    if "error" in r2:
        return 2
    print("  [plan]     ...", flush=True)
    r4 = run_plan_generator(sta)
    print(f"  [plan]     {r4}", flush=True)
    if "error" in r4:
        return 2
    print("  [done] manifest + plan rebuilt", flush=True)
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
    p.add_argument("--no-time-index", action="store_true",
                   help="skip the per-trace time-index build (faster bulk-add). "
                        "Run rebuild_time_index later to backfill.")
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

    p = sub.add_parser("rebuild", help="re-run level1+plan_generator against current mirror (use after code changes)")
    p.add_argument("sta")
    p.set_defaults(func=cmd_rebuild)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

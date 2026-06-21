#!/usr/bin/env python3
"""test_env_run.py — invoke production phase3 against the test environment.

For each registered (STA, YYYY-MM-DD) in the test env catalogue, this:

  1. PRE-FLIGHT — checks the test env's manifest DB for anomalies:
       - rows with recorder_type IN ('unknown', NULL)
       - rows with source_type IN ('unknown', NULL)
       - rows with role values outside {waveform, metadata}
       - rows with exclude_reason values outside the documented set
       - file-count mismatch between disk and manifest
     Surfaces these BEFORE phase3 runs. If anomalies exist, requires
     --force to proceed (so we notice new file-name variants instead of
     silently ignoring them).

  2. STAGING WIPE — clears any prior test env staging output for the day,
     so the run is idempotent.

  3. PHASE3 INVOCATION — same production binary, same arguments shape
     as the orchestrator uses. --workers=1 for determinism. Output goes
     to the test env's CIFS staging path (so dev1 can read for the
     dry-run apply step later).

  4. POST-RUN — reads the emitted run-manifest JSON, prints the per-date
     status (n_files, n_traces, status, error).

USAGE:
  python3 scan/test_env_run.py convert STBK 2022-10-23
  python3 scan/test_env_run.py convert HOLS 2018-06-15 --force-anomalies
  python3 scan/test_env_run.py preflight STBK 2022-10-23

Output writes to <TEST_ENV_LOCAL>/logs/<RUN_ID>.log per run.
"""
from __future__ import annotations
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# Re-use test_env_build's constants — same module on disk
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_env_build import (  # noqa: E402
    TEST_ENV_LOCAL, MIRROR_SRC, MANIFEST_DBS, PLANS, LOGS, QUEUE,
    TEST_ENV_SHARED, STAGING_SDS, REPO, REGISTRY, PYTHON, load_catalogue,
)

PHASE3 = REPO / "scan" / "phase3_driver.py"

# Documented allowed values for manifest columns. Anything outside is flagged.
ALLOWED_RECORDER_TYPES = {"echopro", "gecko", "mseed"}
ALLOWED_SOURCE_TYPES = {"disk", "telemetry"}
ALLOWED_ROLES = {"waveform", "metadata"}
# exclude_reason can be NULL (kept), or one of these documented exclusions.
# Anything else surfaces as anomaly.
ALLOWED_EXCLUDE_REASONS = {
    None, "triggered", "single_channel", "wrong_station", "wrong_extension",
    "wrong_date_filename", "filename_unparseable",
}


# --------------------------------------------------------------------------
# Pre-flight: surface anomalies in the manifest
# --------------------------------------------------------------------------

def preflight(sta: str, year: int, month: int, day: int) -> dict:
    """Inspect the test env manifest for the (sta, year, month, day) we're
    about to convert. Returns a dict with anomaly buckets, each a list of
    sample paths."""
    db_path = MANIFEST_DBS / f"VW.{sta}.db"
    if not db_path.exists():
        return {"error": f"manifest DB missing: {db_path}"}

    conn = sqlite3.connect(db_path)
    findings: dict = {
        "total_manifest_rows": 0,
        "n_files_on_disk": 0,
        "by_recorder_type": {},
        "by_source_type": {},
        "by_role": {},
        "by_exclude_reason": {},
        "anomalies": {
            "unknown_recorder_type": [],
            "null_recorder_type": [],
            "unknown_source_type": [],
            "null_source_type": [],
            "non_standard_role": [],
            "non_standard_exclude_reason": [],
            "date_mismatch": [],
        },
    }

    # File count on disk for sanity
    day_dir = MIRROR_SRC / sta / "continuous" / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
    findings["n_files_on_disk"] = len(list(day_dir.iterdir())) if day_dir.exists() else 0

    # Total manifest rows for this day
    findings["total_manifest_rows"] = conn.execute(
        "SELECT COUNT(*) FROM files WHERE station=? AND dir_year=? "
        "AND dir_month=? AND dir_day=?",
        (sta, year, month, day)).fetchone()[0]

    # Breakdown counts
    for col, target in [("recorder_type", "by_recorder_type"),
                        ("source_type", "by_source_type"),
                        ("role", "by_role"),
                        ("exclude_reason", "by_exclude_reason")]:
        rows = conn.execute(
            f"SELECT {col}, COUNT(*) FROM files "
            f"WHERE station=? AND dir_year=? AND dir_month=? AND dir_day=? "
            f"GROUP BY {col}",
            (sta, year, month, day)).fetchall()
        findings[target] = {(r[0] if r[0] is not None else "NULL"): r[1]
                            for r in rows}

    # Anomaly samples (limit 5 per bucket so we don't drown in output)
    queries = [
        ("unknown_recorder_type",
         "SELECT path FROM files WHERE station=? AND dir_year=? "
         "AND dir_month=? AND dir_day=? AND recorder_type='unknown' LIMIT 5"),
        ("null_recorder_type",
         "SELECT path FROM files WHERE station=? AND dir_year=? "
         "AND dir_month=? AND dir_day=? AND recorder_type IS NULL LIMIT 5"),
        ("unknown_source_type",
         "SELECT path FROM files WHERE station=? AND dir_year=? "
         "AND dir_month=? AND dir_day=? AND source_type='unknown' LIMIT 5"),
        ("null_source_type",
         "SELECT path FROM files WHERE station=? AND dir_year=? "
         "AND dir_month=? AND dir_day=? AND source_type IS NULL LIMIT 5"),
        ("date_mismatch",
         "SELECT path FROM files WHERE station=? AND dir_year=? "
         "AND dir_month=? AND dir_day=? AND date_mismatch=1 LIMIT 5"),
    ]
    params = (sta, year, month, day)
    for key, sql in queries:
        findings["anomalies"][key] = [r[0].split("/")[-1]
                                       for r in conn.execute(sql, params).fetchall()]

    # role / exclude_reason that aren't in the documented set
    rows = conn.execute(
        "SELECT path, role FROM files WHERE station=? AND dir_year=? "
        "AND dir_month=? AND dir_day=? AND role NOT IN ('waveform','metadata') LIMIT 5",
        params).fetchall()
    findings["anomalies"]["non_standard_role"] = [
        f"{r[0].split('/')[-1]} (role={r[1]})" for r in rows]

    rows = conn.execute(
        "SELECT path, exclude_reason FROM files WHERE station=? AND dir_year=? "
        "AND dir_month=? AND dir_day=? AND exclude_reason IS NOT NULL "
        f"AND exclude_reason NOT IN ({','.join('?' for _ in ALLOWED_EXCLUDE_REASONS if _ is not None)}) "
        "LIMIT 5",
        params + tuple(r for r in ALLOWED_EXCLUDE_REASONS if r is not None)).fetchall()
    findings["anomalies"]["non_standard_exclude_reason"] = [
        f"{r[0].split('/')[-1]} (reason={r[1]})" for r in rows]

    conn.close()

    # Blocking anomalies — truly unfamiliar classifications. These are the
    # ones that should refuse to proceed without --force.
    blocking_buckets = ("unknown_recorder_type", "null_recorder_type",
                        "unknown_source_type", "null_source_type",
                        "non_standard_role", "non_standard_exclude_reason")
    has_blocking_anomalies = any(findings["anomalies"][b] for b in blocking_buckets)
    # date_mismatch is a known per-row flag (session boundary files etc.) — note
    # it but don't block on it.
    has_informational = any(
        findings["anomalies"][b] for b in findings["anomalies"]
        if b not in blocking_buckets)
    findings["has_blocking_anomalies"] = has_blocking_anomalies
    findings["has_informational_anomalies"] = has_informational
    findings["count_mismatch"] = (
        findings["n_files_on_disk"] != findings["total_manifest_rows"])
    return findings


def print_preflight(f: dict) -> None:
    print(f"  [preflight] files on disk:      {f['n_files_on_disk']}", flush=True)
    print(f"  [preflight] manifest rows:      {f['total_manifest_rows']}"
          f"{'  ! mismatch' if f.get('count_mismatch') else ''}", flush=True)
    print(f"  [preflight] by recorder_type:   {f['by_recorder_type']}", flush=True)
    print(f"  [preflight] by source_type:     {f['by_source_type']}", flush=True)
    print(f"  [preflight] by role:            {f['by_role']}", flush=True)
    print(f"  [preflight] by exclude_reason:  {f['by_exclude_reason']}", flush=True)
    blocking_buckets = ("unknown_recorder_type", "null_recorder_type",
                        "unknown_source_type", "null_source_type",
                        "non_standard_role", "non_standard_exclude_reason")
    if f.get("has_blocking_anomalies"):
        print(f"  [preflight] BLOCKING ANOMALIES:", flush=True)
        for bucket in blocking_buckets:
            samples = f["anomalies"].get(bucket, [])
            if samples:
                print(f"               {bucket} ({len(samples)} sample{'s' if len(samples)!=1 else ''}):")
                for s in samples:
                    print(f"                 - {s}")
    if f.get("has_informational_anomalies"):
        print(f"  [preflight] informational:", flush=True)
        for bucket, samples in f["anomalies"].items():
            if bucket in blocking_buckets or not samples:
                continue
            print(f"               {bucket} ({len(samples)} sample{'s' if len(samples)!=1 else ''}):")
            for s in samples:
                print(f"                 - {s}")
    if not f.get("has_blocking_anomalies") and not f.get("has_informational_anomalies"):
        print(f"  [preflight] no anomalies", flush=True)


# --------------------------------------------------------------------------
# Staging wipe
# --------------------------------------------------------------------------

def wipe_staging_day(sta: str, year: int) -> dict:
    """Clear the test-env staging SDS subtree for one (sta, year)."""
    target = STAGING_SDS / f"{year:04d}" / "VW" / sta
    if target.exists():
        shutil.rmtree(target)
        return {"wiped": str(target)}
    return {"wiped": None}


# --------------------------------------------------------------------------
# Phase3 invocation
# --------------------------------------------------------------------------

def run_phase3(sta: str, year: int, month: int, day: int, run_id: str) -> dict:
    db_path = MANIFEST_DBS / f"VW.{sta}.db"
    plan_path = PLANS / f"VW.{sta}.plan.yaml"
    run_manifest = QUEUE / "run_manifests" / f"{run_id}.json"
    run_manifest.parent.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{run_id}.log"
    date_str = f"{year:04d}-{month:02d}-{day:02d}"

    STAGING_SDS.mkdir(parents=True, exist_ok=True)

    cmd = [PYTHON, "-u", str(PHASE3),
           str(db_path), str(plan_path),
           "--registry", str(REGISTRY),
           "--staging-sds", str(STAGING_SDS),
           "--workers", "1",
           "--commit",
           "--start-date", date_str,
           "--end-date", date_str,
           "--run-manifest", str(run_manifest)]

    t0 = time.time()
    with log_path.open("w") as logf:
        logf.write(f"# cmd: {' '.join(cmd)}\n")
        logf.flush()
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    elapsed = round(time.time() - t0, 1)
    return {
        "rc": proc.returncode,
        "elapsed_s": elapsed,
        "log_path": str(log_path),
        "run_manifest_path": str(run_manifest),
    }


def summarize_run_manifest(run_manifest_path: str) -> dict:
    p = Path(run_manifest_path)
    if not p.exists():
        return {"error": f"run manifest not written: {p}"}
    d = json.loads(p.read_text())
    eq = d.get("eqserver", {})
    pds = eq.get("per_date_status", [])
    return {
        "items_succeeded": d.get("aggregate", {}).get("items_succeeded"),
        "items_failed": d.get("aggregate", {}).get("items_failed"),
        "bytes_written": d.get("aggregate", {}).get("bytes_written"),
        "per_date": [
            {"date": p["date"], "status": p["status"],
             "n_files": p["n_files"], "n_traces": p["n_traces"],
             "error": p.get("error", "")[:80]}
            for p in pds
        ],
    }


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_preflight(args):
    year, month, day = (int(p) for p in args.date.split("-"))
    print(f"[preflight] {args.sta} {args.date}", flush=True)
    f = preflight(args.sta, year, month, day)
    if "error" in f:
        print(f"  ERROR: {f['error']}", flush=True)
        return 2
    print_preflight(f)
    return 0


def cmd_convert(args):
    sta = args.sta
    year, month, day = (int(p) for p in args.date.split("-"))
    print(f"[convert] {sta} {args.date}", flush=True)

    # Verify the day is registered in the catalogue (caught accidentally
    # converting a day that wasn't built).
    entries = load_catalogue()
    if not any(e["sta"] == sta and e["year"] == year and e["month"] == month
               and e["day"] == day for e in entries):
        print(f"  ERROR: not registered in catalogue. "
              f"Run test_env_build.py add first.", flush=True)
        return 2

    # 1. Pre-flight
    f = preflight(sta, year, month, day)
    if "error" in f:
        print(f"  preflight error: {f['error']}", flush=True)
        return 2
    print_preflight(f)
    if f["has_blocking_anomalies"] and not args.force_anomalies:
        print(f"  REFUSING TO PROCEED: pre-flight found UNKNOWN classifications "
              f"(unfamiliar recorder_type/source_type/role/exclude_reason).\n"
              f"  Re-run with --force-anomalies to ignore, or update level1.py / "
              f"test_env_build.py to recognise the new pattern.", flush=True)
        return 2

    # 2. Wipe prior staging output for this day
    print(f"  [wipe]      clearing test env staging for {sta} {year}", flush=True)
    print(f"  [wipe]      {wipe_staging_day(sta, year)}", flush=True)

    # 3. Phase3
    run_id = f"test_VW_{sta}_{year}-{month:02d}-{day:02d}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    print(f"  [phase3]    run_id={run_id}", flush=True)
    print(f"  [phase3]    invoking phase3_driver.py (workers=1, commit, single day) ...",
          flush=True)
    r = run_phase3(sta, year, month, day, run_id)
    print(f"  [phase3]    rc={r['rc']}  elapsed={r['elapsed_s']}s  log={r['log_path']}",
          flush=True)
    if r["rc"] != 0:
        print(f"  PHASE3 FAILED — see log", flush=True)
        return r["rc"]

    # 4. Post-run summary from the run manifest
    s = summarize_run_manifest(r["run_manifest_path"])
    if "error" in s:
        print(f"  [summary]   {s['error']}", flush=True)
        return 2
    print(f"  [summary]   items_succeeded={s['items_succeeded']}  "
          f"items_failed={s['items_failed']}  "
          f"bytes_written={s['bytes_written']}", flush=True)
    for d in s["per_date"]:
        print(f"               {d['date']}  status={d['status']}  "
              f"n_files={d['n_files']}  n_traces={d['n_traces']}"
              f"{'  err='+d['error'] if d['error'] else ''}",
              flush=True)
    print(f"  [done]      staging output at "
          f"{STAGING_SDS}/{year:04d}/VW/{sta}/", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight",
                       help="inspect manifest for anomalies (read-only)")
    p.add_argument("sta")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("convert",
                       help="pre-flight + wipe + run phase3 + summarize")
    p.add_argument("sta")
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("--force-anomalies", action="store_true",
                   help="proceed even if pre-flight found anomalies "
                        "(default: refuse so unfamiliar filename patterns "
                        "surface explicitly)")
    p.set_defaults(func=cmd_convert)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

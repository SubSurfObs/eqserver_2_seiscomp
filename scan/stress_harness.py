#!/usr/bin/env python3
"""Random-sample stress test for the Level-1 scanner + Phase-2b classifier.

For N iterations:
  1. Pick a random date in [START, END].
  2. Scan all `include:true` stations for that day → rolling /tmp/stress/run.db
  3. Query the DB through check_manifest.classify(). Capture:
       - classification breakdown
       - per-station classification
       - edge cases (any station with > MIN_INTERESTING_FILES but not in CLEAN_CATEGORIES)
  4. Append summary line to samples.jsonl, edge rows to edges.jsonl.

Tail `/tmp/stress/harness.log` to monitor.

Stops cleanly on Ctrl+C; output written incrementally so partial runs are
useful. Rolling DB is deleted at the end.
"""
from __future__ import annotations
import argparse
import json
import os
import random
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_manifest import (  # noqa: E402
    classify,
    CLEAN_CATEGORIES,
    PER_DAY_SQL,
    MINIMUS_STATIONS_DEFAULT,
)

MIN_INTERESTING_FILES = 100
SCAN_TIMEOUT_S = 600

_should_stop = False


def _on_sigint(signum, frame):
    global _should_stop
    print("\n[harness] received SIGINT — finishing current iteration then stopping", flush=True)
    _should_stop = True


def run_one_sample(d, stations_file, scanner_path, db_path, workers):
    """Scan + classify one random date. Returns a dict ready to JSON-serialise."""
    # Clean prior run's artefacts
    if os.path.exists(db_path):
        os.remove(db_path)
    parts_dir = db_path + ".parts"
    if os.path.isdir(parts_dir):
        shutil.rmtree(parts_dir)

    t0 = time.time()
    cmd = [
        "python3", scanner_path,
        "--stations-file", stations_file,
        "--year", str(d.year),
        "--month", str(d.month),
        "--day", str(d.day),
        "--workers", str(workers),
        "--db", db_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=SCAN_TIMEOUT_S, text=True)
    except subprocess.TimeoutExpired:
        return {"date": str(d), "error": "timeout", "scan_elapsed_s": SCAN_TIMEOUT_S}
    except subprocess.CalledProcessError as e:
        return {"date": str(d), "error": f"scan-failed: {(e.stderr or '')[:200]}"}
    scan_elapsed = round(time.time() - t0, 2)

    if not os.path.exists(db_path):
        return {"date": str(d), "scan_elapsed_s": scan_elapsed,
                "n_station_days": 0, "note": "no archive data for this date"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(PER_DAY_SQL))
    conn.close()

    cls = Counter()
    per_station_class = {}
    edges = []
    total_files = 0
    for r in rows:
        sc = "minimus" if r["station"] in MINIMUS_STATIONS_DEFAULT else None
        c = classify(r, station_class=sc)
        cls[c] += 1
        per_station_class[r["station"]] = c
        files_here = (r["n_disk_ok"] or 0) + (r["n_tele_ok"] or 0)
        total_files += files_here
        if c not in CLEAN_CATEGORIES and files_here >= MIN_INTERESTING_FILES:
            edges.append({
                "station": r["station"],
                "date": str(d),
                "classification": c,
                "n_disk_ok": r["n_disk_ok"], "n_tele_ok": r["n_tele_ok"],
                "n_ss_disk": r["n_ss_disk"], "n_ss_tele": r["n_ss_tele"],
                "n_hhmm_disk": r["n_hhmm_disk"], "n_hhmm_tele": r["n_hhmm_tele"],
                "n_hhmm_union": r["n_hhmm_union"],
                "n_single_chan": r["n_single_chan"],
                "n_triggered": r["n_triggered"],
                "n_wrong_sta": r["n_wrong_sta"],
                "n_unknown_ext": r["n_unknown_ext"],
            })

    return {
        "date": str(d),
        "scan_elapsed_s": scan_elapsed,
        "n_station_days": len(rows),
        "n_total_files": total_files,
        "n_clean": sum(cls[c] for c in CLEAN_CATEGORIES if c in cls),
        "classifications": dict(cls),
        "n_edges": len(edges),
        "per_station_class": per_station_class,
        "edges": edges,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations-file", required=True)
    ap.add_argument("--scanner", required=True, help="path to scan/level1.py")
    ap.add_argument("--samples", type=int, default=200)
    ap.add_argument("--start-date", default="2014-01-01")
    ap.add_argument("--end-date", default="2026-05-01")
    ap.add_argument("--output-dir", default="/tmp/stress")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--seed", type=int, default=0, help="0 = random; non-zero = deterministic")
    args = ap.parse_args()

    if args.seed:
        random.seed(args.seed)

    os.makedirs(args.output_dir, exist_ok=True)
    db_path = os.path.join(args.output_dir, "run.db")
    samples_jsonl = os.path.join(args.output_dir, "samples.jsonl")
    edges_jsonl = os.path.join(args.output_dir, "edges.jsonl")

    start_d = date.fromisoformat(args.start_date)
    end_d = date.fromisoformat(args.end_date)
    delta_days = (end_d - start_d).days

    signal.signal(signal.SIGINT, _on_sigint)

    print(f"[harness] starting {args.samples} samples from {start_d} to {end_d}", flush=True)
    print(f"[harness] stations: {args.stations_file}", flush=True)
    print(f"[harness] scanner:  {args.scanner}", flush=True)
    print(f"[harness] workers:  {args.workers}", flush=True)
    print(f"[harness] output:   {args.output_dir}/", flush=True)
    print(f"[harness] writing samples.jsonl + edges.jsonl incrementally", flush=True)

    t_start = time.time()
    n_done = 0
    n_errors = 0
    n_total_edges = 0
    for i in range(args.samples):
        if _should_stop:
            break
        d = start_d + timedelta(days=random.randint(0, delta_days))
        try:
            result = run_one_sample(d, args.stations_file, args.scanner, db_path, args.workers)
        except Exception as e:
            result = {"date": str(d), "error": f"harness-exception: {type(e).__name__}: {e}"}
        n_done += 1
        elapsed = time.time() - t_start
        per_sample = elapsed / n_done
        eta = per_sample * (args.samples - n_done)

        if "error" in result:
            n_errors += 1
            print(f"  [{i+1}/{args.samples}] {d}  ERROR  {result['error'][:100]}", flush=True)
        else:
            n_sd = result["n_station_days"]
            n_clean = result.get("n_clean", 0)
            pct = (100 * n_clean / n_sd) if n_sd else 0.0
            n_edges = result.get("n_edges", 0)
            n_total_edges += n_edges
            print(f"  [{i+1}/{args.samples}] {d}  sta-days={n_sd:>3}  clean={n_clean:>3} ({pct:5.1f}%)  edges={n_edges:>2}  scan={result['scan_elapsed_s']:>5.1f}s  elap={elapsed/60:5.1f}m  eta={eta/60:5.1f}m", flush=True)

        # Append summary line (drop verbose per_station_class + edges from samples.jsonl)
        summary = {k: v for k, v in result.items() if k not in ("edges",)}
        with open(samples_jsonl, "a") as f:
            f.write(json.dumps(summary) + "\n")
        for e in result.get("edges", []):
            with open(edges_jsonl, "a") as f:
                f.write(json.dumps(e) + "\n")

    # Cleanup rolling DB
    for p in (db_path, db_path + ".parts"):
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)

    print(f"\n[harness] done: {n_done} samples, {n_errors} errors, {n_total_edges} edge cases, "
          f"total {(time.time() - t_start)/60:.1f} min", flush=True)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""diagnose.py — Phase B wrapper: run all per-station diagnostics.

Phase B sub-stages run in dependency order:
  1. categorize_source.py     -> metadata/source_stats/<STA>.json
  2. discovery_audit.py       -> metadata/source_stats/_discovery/<STA>.json
  3. investigate_unmatched.py -> metadata/source_stats/_unmatched/<STA>.json
  4. channel_epoch_scan.py    -> metadata/source_stats/_epochs/<STA>.json
  5. size_stats.py            -> enriches metadata/source_stats/_epochs/<STA>.json

Idempotent: skips a sub-stage when its output file already exists AND is
newer than the manifest DB. Use --force to re-run regardless.

Single per-station entry point — one command, one progress trail, no
hidden state between sub-stages.

USAGE:
  python3 scan/diagnose.py STBK
  python3 scan/diagnose.py STBK --force
  python3 scan/diagnose.py --all                          # all VW stations
  python3 scan/diagnose.py --all --network DU
  python3 scan/diagnose.py --all --stations BEST,STBK,HOLS
  python3 scan/diagnose.py STBK --db-dir /path --out-dir /path
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
DEFAULT_OUT_DIR = ROOT / "metadata" / "source_stats"


# Per-station sub-stages. Run for each station in order; idempotent skip
# when output is newer than the manifest DB.
PER_STATION_SUBSTAGES = [
    {
        "name": "categorize_source",
        "script": HERE / "categorize_source.py",
        "outputs": lambda out_dir, sta: [out_dir / f"{sta}.json"],
    },
    {
        "name": "discovery_audit",
        "script": HERE / "discovery_audit.py",
        "outputs": lambda out_dir, sta: [out_dir / "_discovery" / f"{sta}.json"],
    },
    {
        "name": "channel_epoch_scan",
        "script": HERE / "channel_epoch_scan.py",
        "outputs": lambda out_dir, sta: [out_dir / "_epochs" / f"{sta}.json"],
    },
    {
        "name": "size_stats",
        "script": HERE / "size_stats.py",
        "outputs": lambda out_dir, sta: [out_dir / "_epochs" / f"{sta}.json"],
        # size_stats enriches _epochs/<STA>.json — same file as channel_epoch_scan
        # plus an added size_stats block. Mtime check alone won't tell us if
        # size_stats has run yet; require a content marker key.
        "content_marker": "size_stats_added_at_utc",
    },
]

# Network-wide sub-stage. Runs once AFTER all per-station discovery_audit
# outputs exist (it reads every _discovery/<STA>.json). Only invoked in
# --all mode.
NETWORK_SUBSTAGE = {
    "name": "investigate_unmatched",
    "script": HERE / "investigate_unmatched.py",
    "primary_output": lambda out_dir: out_dir / "_unmatched" / "_network_summary.json",
}


def output_is_fresh(stage: dict, output_path: Path, manifest_db: Path) -> bool:
    """Return True if the stage's output is up-to-date relative to the DB."""
    if not output_path.exists():
        return False
    if not manifest_db.exists():
        return True
    if output_path.stat().st_mtime <= manifest_db.stat().st_mtime:
        return False
    # If this stage writes a content marker, require it to be present.
    marker = stage.get("content_marker")
    if marker:
        try:
            d = json.loads(output_path.read_text())
            if marker not in d:
                return False
        except Exception:
            return False
    return True


def diagnose_station(sta: str, network: str, db_dir: Path, out_dir: Path,
                    force: bool, python_exec: str) -> dict:
    """Run all Phase B sub-stages for one station, in order, idempotently."""
    db_path = db_dir / f"{network}.{sta}.db"
    if not db_path.exists():
        return {"sta": sta, "skipped": True,
                "reason": f"manifest DB not found: {db_path}"}

    print(f"\n=== diagnose {sta} (DB: {db_path}) ===", flush=True)
    results = []
    for stage in PER_STATION_SUBSTAGES:
        name = stage["name"]
        script = stage["script"]
        outputs = stage["outputs"](out_dir, sta)
        primary_out = outputs[0]

        if not force and output_is_fresh(stage, primary_out, db_path):
            print(f"  [{name:25s}] SKIP   (output newer than DB)", flush=True)
            results.append({"stage": name, "status": "skipped",
                           "output": str(primary_out)})
            continue

        cmd = [python_exec, str(script), sta,
               "--network", network,
               "--db-dir", str(db_dir),
               "--out-dir", str(_out_dir_for_stage(stage, out_dir))]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            elapsed = time.time() - t0
            if r.returncode == 0:
                print(f"  [{name:25s}] OK     ({elapsed:6.1f}s) -> {primary_out.name}",
                      flush=True)
                results.append({"stage": name, "status": "ok",
                               "elapsed_s": round(elapsed, 1),
                               "output": str(primary_out)})
            else:
                print(f"  [{name:25s}] FAIL   rc={r.returncode}", flush=True)
                tail = r.stderr.strip().splitlines()[-3:] if r.stderr else []
                for line in tail:
                    print(f"    {line}", flush=True)
                results.append({"stage": name, "status": "fail",
                               "rc": r.returncode,
                               "stderr_tail": "\n".join(tail)})
                # Continue to next sub-stage even on fail — they may not
                # depend on each other. size_stats DOES depend on
                # channel_epoch_scan having produced output, but it will
                # gracefully report n_samples=0 if the input is missing.
        except subprocess.TimeoutExpired:
            print(f"  [{name:25s}] TIMEOUT (>900s)", flush=True)
            results.append({"stage": name, "status": "timeout"})
        except Exception as exc:
            print(f"  [{name:25s}] EXCEPT {type(exc).__name__}: {exc}", flush=True)
            results.append({"stage": name, "status": "exception",
                           "error": str(exc)})

    n_ok = sum(1 for r in results if r["status"] in ("ok", "skipped"))
    n_fail = len(results) - n_ok
    return {"sta": sta, "n_ok": n_ok, "n_fail": n_fail, "results": results}


def _out_dir_for_stage(stage: dict, base_out_dir: Path) -> Path:
    """Most sub-stages write to base_out_dir; channel_epoch and size_stats
    write to base_out_dir/_epochs/. discovery_audit writes to base_out_dir/
    _discovery/. investigate_unmatched writes to base_out_dir/_unmatched/.

    For uniformity, every sub-script accepts --out-dir as its own target
    directory (the parent of its <STA>.json file). So we hand each sub-stage
    its appropriate sub-directory."""
    name = stage["name"]
    if name == "channel_epoch_scan":
        return base_out_dir / "_epochs"
    if name == "size_stats":
        return base_out_dir / "_epochs"
    if name == "discovery_audit":
        return base_out_dir / "_discovery"
    return base_out_dir


def run_network_substage(network: str, db_dir: Path, out_dir: Path,
                         force: bool, python_exec: str) -> dict:
    """Run the network-wide investigate_unmatched after per-station stages."""
    stage = NETWORK_SUBSTAGE
    name = stage["name"]
    script = stage["script"]
    out = stage["primary_output"](out_dir)

    print(f"\n=== network-wide: {name} ===", flush=True)
    if not force and out.exists():
        # Find the most recent _discovery/<STA>.json mtime — if our network
        # summary is newer than all inputs, we can skip.
        disc_dir = out_dir / "_discovery"
        if disc_dir.is_dir():
            newest_input = max((p.stat().st_mtime for p in disc_dir.glob("*.json")),
                              default=0)
            if out.stat().st_mtime > newest_input:
                print(f"  [{name:25s}] SKIP (output newer than all _discovery inputs)",
                      flush=True)
                return {"stage": name, "status": "skipped", "output": str(out)}

    cmd = [python_exec, str(script),
           "--network", network,
           "--db-dir", str(db_dir),
           "--out-dir", str(out_dir / "_unmatched")]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        elapsed = time.time() - t0
        if r.returncode == 0:
            print(f"  [{name:25s}] OK     ({elapsed:6.1f}s) -> {out.name}",
                  flush=True)
            return {"stage": name, "status": "ok",
                   "elapsed_s": round(elapsed, 1), "output": str(out)}
        else:
            tail = r.stderr.strip().splitlines()[-3:] if r.stderr else []
            print(f"  [{name:25s}] FAIL rc={r.returncode}", flush=True)
            for line in tail:
                print(f"    {line}", flush=True)
            return {"stage": name, "status": "fail", "rc": r.returncode,
                   "stderr_tail": "\n".join(tail)}
    except subprocess.TimeoutExpired:
        print(f"  [{name:25s}] TIMEOUT (>1800s)", flush=True)
        return {"stage": name, "status": "timeout"}
    except Exception as exc:
        print(f"  [{name:25s}] EXCEPT {type(exc).__name__}: {exc}", flush=True)
        return {"stage": name, "status": "exception", "error": str(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta", nargs="?",
                   help="station code (omit with --all)")
    ap.add_argument("--all", action="store_true",
                   help="run on every <NET>.*.db in --db-dir")
    ap.add_argument("--stations", default=None,
                   help="comma-separated station filter (use with --all)")
    ap.add_argument("--network", default="VW")
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--force", action="store_true",
                   help="re-run all sub-stages, ignoring idempotency")
    ap.add_argument("--python", default=sys.executable,
                   help="python interpreter to invoke sub-scripts with")
    ap.add_argument("--summary-json", default=None,
                   help="write a per-station summary JSON to this path")
    args = ap.parse_args()

    if args.all:
        if not args.db_dir.is_dir():
            print(f"ERROR: db-dir not found: {args.db_dir}", file=sys.stderr)
            return 2
        stations = sorted({p.stem.split(".")[1]
                          for p in args.db_dir.glob(f"{args.network}.*.db")})
        if args.stations:
            wanted = set(args.stations.split(","))
            stations = [s for s in stations if s in wanted]
    else:
        if not args.sta:
            print("ERROR: provide station code or --all", file=sys.stderr)
            return 2
        stations = [args.sta]

    t0_all = time.time()
    print(f"[diagnose] {len(stations)} station(s) to process; "
          f"net={args.network} db_dir={args.db_dir} out_dir={args.out_dir}",
          flush=True)

    summary = {
        "started_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "network": args.network,
        "n_stations": len(stations),
        "per_station": [],
    }
    n_ok = n_fail = 0
    for sta in stations:
        r = diagnose_station(sta, args.network, args.db_dir, args.out_dir,
                            args.force, args.python)
        summary["per_station"].append(r)
        if r.get("skipped") or r.get("n_fail", 0) > 0:
            n_fail += 1
        else:
            n_ok += 1
    summary["n_ok"] = n_ok
    summary["n_fail"] = n_fail

    # Network-wide sub-stage runs once after all per-station stages.
    if args.all:
        net_r = run_network_substage(args.network, args.db_dir, args.out_dir,
                                    args.force, args.python)
        summary["network_stage"] = net_r

    summary["elapsed_s"] = round(time.time() - t0_all, 1)

    print(f"\n[diagnose] DONE: {n_ok} ok, {n_fail} fail, "
          f"elapsed {summary['elapsed_s']}s", flush=True)

    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary, indent=2))
        print(f"[diagnose] summary written to {args.summary_json}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

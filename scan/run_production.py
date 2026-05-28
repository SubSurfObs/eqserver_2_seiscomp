#!/usr/bin/env python3
"""Production orchestrator — one station at a time, workers never idle.

Iterates the per-station DBs sequentially. For each station:
  1. Convert all clean days via phase3_driver.py (workers=N, imap_unordered
     keeps the pool busy; only the per-station tail is unavoidable idle).
  2. (optional --promote) promote staging -> long-term archive via the shared
     sds_staging_ledger/apply.py, then clear the station's staging subtree.
  3. Record the station as done in a resume-state file so a restart skips it.

Design choices (per operator, 2026-05-28):
  - ONE station at a time. No multi-station parallelism — at production
    day-counts the per-station tail imbalance is <1%, so one-station-at-a-time
    with a busy worker pool has no fundamental disadvantage, and it preserves
    the clean per-station staging->verify->promote boundary the ledger wants.
  - The ~5-10s inter-station gap (pool teardown + next plan load) is accepted
    (~15 min total across the whole archive) rather than adding a persistent-
    pool refactor that would blur the per-station boundary.

Resume: re-running skips stations already in the state file. Safe to Ctrl-C
and restart; a station mid-conversion just re-runs (phase3 overwrites days).

Run (stage only, default — review then promote separately):
  python3 scan/run_production.py \
      --station-dbs /home/.../station_dbs \
      --plans /tmp/plans_vw \
      --registry metadata/station_registry.yaml \
      --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
      --workers 8 --networks VW

Run (stage + promote + clear staging per station):
  ... --promote --lt-root /mnt/seiscomp_archive \
      --ledger-root /home/.../sds_staging_ledger/seiscomp_archive
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3 = os.path.join(HERE, "phase3_driver.py")
DEFAULT_VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"
DEFAULT_APPLY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/sds_staging_ledger/apply.py"


def load_state(path):
    if os.path.exists(path):
        return json.load(open(path))
    return {"done": {}, "started": datetime.utcnow().isoformat()}


def save_state(path, state):
    tmp = path + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2)
    os.replace(tmp, path)


def discover_stations(station_dbs, plans, networks):
    """Return ordered [(net, sta, db_path, plan_path)] for stations that have
    both a DB and a plan, filtered to the requested networks, plan status not
    defer_conversion."""
    import yaml
    out = []
    for f in sorted(os.listdir(station_dbs)):
        if not f.endswith(".db"):
            continue
        parts = f[:-3].split(".")
        if len(parts) != 2:
            continue
        net, sta = parts
        if networks and net not in networks:
            continue
        plan_path = os.path.join(plans, f"{net}.{sta}.plan.yaml")
        if not os.path.exists(plan_path):
            continue
        status = (yaml.safe_load(open(plan_path)) or {}).get("status")
        if status == "defer_conversion":
            continue
        out.append((net, sta, os.path.join(station_dbs, f), plan_path))
    return out


def convert_station(net, sta, db, plan, args):
    cmd = [
        args.python, "-u", PHASE3, db, plan,
        "--registry", args.registry,
        "--staging-sds", args.staging_sds,
        "--workers", str(args.workers),
        "--disk-size-floor-ratio", str(args.disk_size_floor_ratio),
        "--commit",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def promote_station(net, sta, args):
    cmd = [
        args.python, "-u", args.apply_py,
        "--staging-root", args.staging_sds,
        "--lt-root", args.lt_root,
        "--ledger-root", args.ledger_root,
        "--net", net, "--sta", sta,
        "--source-kind", "eqserver",
        "--source-card", f"eqserver_{net}_{sta}_{datetime.utcnow().date().isoformat()}",
        "--mode", "decide",
        "--commit",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def clear_station_staging(net, sta, staging_sds):
    """Remove this station's staging subtree across all years. Bounded-disk
    hygiene after a successful promote. Never touches the LT archive."""
    import glob, shutil
    removed = 0
    for ydir in glob.glob(os.path.join(staging_sds, "*", net, sta)):
        shutil.rmtree(ydir, ignore_errors=True)
        removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station-dbs", required=True)
    ap.add_argument("--plans", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--disk-size-floor-ratio", type=float, default=0.8)
    ap.add_argument("--networks", default="VW",
                    help="comma-separated networks to include (default VW)")
    ap.add_argument("--stations", default="",
                    help="optional comma-separated subset (default: all in networks)")
    ap.add_argument("--state-file", default="/tmp/production_state.json")
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    # Promote options
    ap.add_argument("--promote", action="store_true",
                    help="after conversion, promote to LT via apply.py and clear staging")
    ap.add_argument("--lt-root", default="/mnt/seiscomp_archive")
    ap.add_argument("--ledger-root",
                    default="/home/unimelb.edu.au/dsand/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive")
    ap.add_argument("--apply-py", default=DEFAULT_APPLY)
    args = ap.parse_args()

    networks = {n.strip() for n in args.networks.split(",") if n.strip()}
    stations = discover_stations(args.station_dbs, args.plans, networks)
    if args.stations:
        want = {s.strip() for s in args.stations.split(",") if s.strip()}
        stations = [s for s in stations if s[1] in want]

    state = load_state(args.state_file)
    print(f"[production] {len(stations)} stations in scope ({sorted(networks)}), "
          f"workers={args.workers}, promote={args.promote}", flush=True)
    print(f"[production] {len(state['done'])} already done (will skip)", flush=True)

    overall_t0 = time.time()
    for i, (net, sta, db, plan) in enumerate(stations, 1):
        key = f"{net}.{sta}"
        if key in state["done"]:
            print(f"[{i}/{len(stations)}] {key} SKIP (already done)", flush=True)
            continue
        t0 = time.time()
        print(f"\n[{i}/{len(stations)}] {key} converting...", flush=True)
        rc, out, err = convert_station(net, sta, db, plan, args)
        conv_elapsed = time.time() - t0
        # Pull the summary line from phase3 stdout
        summary = next((l for l in out.splitlines() if "days processed" in l), "")
        bytes_line = next((l for l in out.splitlines() if "bytes written" in l), "")
        if rc != 0:
            print(f"  CONVERT FAILED rc={rc} in {conv_elapsed:.0f}s; stderr tail:\n{err[-600:]}", flush=True)
            state["done"][key] = {"status": "convert_failed", "rc": rc,
                                  "elapsed_s": round(conv_elapsed, 1)}
            save_state(args.state_file, state)
            continue
        print(f"  converted in {conv_elapsed:.0f}s | {summary.strip()} | {bytes_line.strip()}", flush=True)

        record = {"status": "converted", "convert_elapsed_s": round(conv_elapsed, 1)}

        if args.promote:
            tp = time.time()
            prc, pout, perr = promote_station(net, sta, args)
            prom_line = next((l for l in pout.splitlines()
                              if l.startswith("write=") or "would write" in l or "write=" in l), "")
            if prc != 0:
                print(f"  PROMOTE FAILED rc={prc}; stderr tail:\n{perr[-400:]}", flush=True)
                record["promote"] = {"status": "failed", "rc": prc}
            else:
                n_cleared = clear_station_staging(net, sta, args.staging_sds)
                print(f"  promoted in {time.time()-tp:.0f}s | {prom_line.strip()} | "
                      f"cleared {n_cleared} staging year-dirs", flush=True)
                record["promote"] = {"status": "ok", "elapsed_s": round(time.time()-tp, 1),
                                     "staging_cleared": n_cleared}

        record["finished"] = datetime.utcnow().isoformat()
        state["done"][key] = record
        save_state(args.state_file, state)

    wall = time.time() - overall_t0
    n_done = sum(1 for v in state["done"].values() if v.get("status") == "converted")
    n_fail = sum(1 for v in state["done"].values() if v.get("status") == "convert_failed")
    print(f"\n[production] run complete in {wall/3600:.1f}h. "
          f"converted={n_done} failed={n_fail} total_recorded={len(state['done'])}", flush=True)
    print(f"[production] state: {args.state_file}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

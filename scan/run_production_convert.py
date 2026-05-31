#!/usr/bin/env python3
"""run_production_convert.py — drives phase3_driver.py per (station, year),
appends a completion event to pending.jsonl on success.

Runs on the **staging VM** (the host that has /mnt/eqserver_archive NFS ro
and /mnt/seiscomp_staging CIFS rw). One half of the cross-host orchestration
(option C in PROGRESS.md). The other halves are:
  - run_production_promote.py  on dev1
  - run_production_cleanup.py  on the staging VM

Iteration order: **station-by-station outer, year-backwards inner** (decided
2026-05-31; see PROGRESS.md). For each (station, year) the script invokes
phase3 with --start-date YYYY-01-01 --end-date YYYY-12-31, captures the
run-manifest, and on success appends one event line to pending.jsonl. The
promote.py watcher on dev1 picks it up within seconds.

Resume: at startup, reads pending.jsonl and skips any (sta, year) tuple
already present. Failed runs are NOT appended to pending.jsonl and will be
retried on restart.

Staging SDS root: /mnt/seiscomp_staging/seiscomp_archive — the SHARED SDS
that disk_to_sds also writes into. Day-files for VW.<STA> end up at:
  /mnt/seiscomp_staging/seiscomp_archive/<YEAR>/VW/<STA>/<CHA>.D/...
Eqserver and disk_to_sds writes don't collide because they own different
<NET>.<STA> subtrees in practice (eqserver runs do legacy stations;
disk_to_sds does SD-card stations).

Queue files (control-plane, NOT seismic data) live alongside seiscomp_archive
at /mnt/seiscomp_staging/eqserver_queue/{pending,promoted,cleaned}.jsonl —
both staging VM and dev1 mount the staging share so both can read/write here.

Run:
  python3 scan/run_production_convert.py \
      --registry metadata/station_registry.yaml \
      --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
      --year-min 2012 --year-max 2025 \
      --workers 4
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
import yaml
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from orchestrate_queue import (
    queue_dir, append_event, read_all_events, build_run_id, utc_now,
)

DEFAULT_VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"
DEFAULT_PHASE3 = HERE / "phase3_driver.py"
SUPPORTED_RECORDERS = {"echopro", "gecko", "minimus", "reftek_rt130"}


def station_years(plan: dict, year_min: int, year_max: int) -> list[int]:
    """Years where the plan has eligible supported-recorder epoch coverage,
    sorted descending (most recent first)."""
    years: set[int] = set()
    for ep in plan.get("epochs", []):
        if ep.get("recorder") not in SUPPORTED_RECORDERS:
            continue
        start_y = int(ep["start"][:4])
        end_y = int(ep["end"][:4])
        for y in range(max(start_y, year_min), min(end_y, year_max) + 1):
            years.add(y)
    return sorted(years, reverse=True)


def run_convert(args, station: str, year: int) -> dict:
    """Invoke phase3 for one (station, year) unit."""
    db_path = Path(args.station_dbs) / f"{args.network}.{station}.db"
    plan_path = Path(args.plans) / f"{args.network}.{station}.plan.yaml"
    if not db_path.exists() or not plan_path.exists():
        return {"status": "skip", "reason": "db or plan missing"}
    run_id = build_run_id(args.network, station, year)
    run_manifest_path = Path(args.run_manifests_dir) / f"{run_id}.json"
    log_path = Path(args.log_dir) / f"{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [args.python, "-u", str(args.phase3), str(db_path), str(plan_path),
           "--registry", args.registry,
           "--staging-sds", args.staging_sds,
           "--workers", str(args.workers),
           "--commit",
           "--start-date", f"{year:04d}-01-01",
           "--end-date", f"{year:04d}-12-31",
           "--run-manifest", str(run_manifest_path)]
    t0 = time.time()
    with log_path.open("w") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    elapsed = time.time() - t0
    # If the run produced zero days (whole year was no_files), the run_manifest
    # is still written — but apply.py would have nothing to promote, so we skip
    # appending to the queue. Distinguish by checking aggregate.items_succeeded.
    items_ok = 0
    if proc.returncode == 0 and run_manifest_path.exists():
        try:
            import json
            with run_manifest_path.open() as f:
                m = json.load(f)
            items_ok = m.get("aggregate", {}).get("items_succeeded", 0)
        except Exception:
            pass
    return {
        "status": "ok" if proc.returncode == 0 else "fail",
        "rc": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "items_succeeded": items_ok,
        "run_id": run_id,
        "run_manifest_path": str(run_manifest_path),
        "log_path": str(log_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", default="VW")
    ap.add_argument("--stations", default="",
                    help="comma-separated subset (default: all stations with a plan + DB)")
    ap.add_argument("--year-min", type=int, default=2012)
    ap.add_argument("--year-max", type=int, default=2025)
    ap.add_argument("--station-dbs", default="/home/unimelb.edu.au/dsand/station_dbs")
    ap.add_argument("--plans", default="/tmp/plans_vw")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True,
                    help="SDS root for the production output. The shared "
                         "staging SDS is /mnt/seiscomp_staging/seiscomp_archive "
                         "(same root disk_to_sds writes into).")
    ap.add_argument("--queue-dir", default=None,
                    help="default: <staging-sds-parent>/eqserver_queue")
    ap.add_argument("--run-manifests-dir", default="/tmp/eqserver_runs")
    ap.add_argument("--log-dir", default="/tmp/eqserver_convert_logs")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--phase3", default=str(DEFAULT_PHASE3))
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    args = ap.parse_args()

    queue = Path(args.queue_dir) if args.queue_dir else \
            queue_dir(Path(args.staging_sds).parent)
    pending = queue / "pending.jsonl"
    queue.mkdir(parents=True, exist_ok=True)

    # Resume: which (sta, year) tuples are already in pending?
    done_sta_year: set[tuple[str, int]] = set()
    for e in read_all_events(pending):
        if "sta" in e and "year" in e:
            done_sta_year.add((e["sta"], int(e["year"])))

    print(f"[convert] network={args.network} workers={args.workers}", flush=True)
    print(f"[convert] queue dir: {queue}", flush=True)
    print(f"[convert] already done in queue: {len(done_sta_year)} (sta, year) tuples", flush=True)

    # Build work list: station alphabetical, year descending
    wanted = {s.strip() for s in args.stations.split(",") if s.strip()}
    work: list[tuple[str, int]] = []
    for plan_path in sorted(Path(args.plans).glob(f"{args.network}.*.plan.yaml")):
        sta = plan_path.stem.split(".")[1]
        if wanted and sta not in wanted:
            continue
        db_path = Path(args.station_dbs) / f"{args.network}.{sta}.db"
        if not db_path.exists():
            print(f"[convert]   skip {sta}: no DB at {db_path}", flush=True)
            continue
        plan = yaml.safe_load(plan_path.open())
        # Honor station-level defer_conversion
        if plan.get("status") == "defer_conversion":
            print(f"[convert]   skip {sta}: status=defer_conversion", flush=True)
            continue
        for year in station_years(plan, args.year_min, args.year_max):
            if (sta, year) in done_sta_year:
                continue
            work.append((sta, year))

    print(f"[convert] work units remaining: {len(work)}", flush=True)
    if not work:
        print(f"[convert] nothing to do", flush=True)
        return 0

    t_overall = time.time()
    n_ok = n_fail = n_empty = 0
    for i, (sta, year) in enumerate(work, 1):
        print(f"[convert] [{i}/{len(work)}] {sta} {year} ...", flush=True)
        r = run_convert(args, sta, year)
        if r["status"] == "ok":
            n_ok += 1
            event = {
                "run_id": r["run_id"],
                "net": args.network,
                "sta": sta,
                "year": year,
                "run_manifest_path": r["run_manifest_path"],
                "staging_root": args.staging_sds,
                "items_succeeded": r["items_succeeded"],
                "elapsed_s": r["elapsed_s"],
                "ts": utc_now(),
            }
            if r["items_succeeded"] == 0:
                n_empty += 1
                # Year had no convertible days. Still record so we don't retry,
                # but mark items_succeeded=0 so promote.py can choose to skip.
            append_event(pending, event)
            print(f"[convert] [{i}/{len(work)}] OK   {sta} {year} "
                  f"{r['elapsed_s']:.0f}s items={r['items_succeeded']}", flush=True)
        else:
            n_fail += 1
            print(f"[convert] [{i}/{len(work)}] FAIL {sta} {year} rc={r['rc']} "
                  f"reason={r.get('reason','')} log={r.get('log_path','')}", flush=True)

    wall = time.time() - t_overall
    print(f"\n[convert] done in {wall/3600:.2f}h. ok={n_ok} fail={n_fail} "
          f"empty={n_empty} total={len(work)}", flush=True)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

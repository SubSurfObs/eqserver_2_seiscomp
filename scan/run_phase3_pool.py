#!/usr/bin/env python3
"""Multi-station Phase 3 launcher.

Runs N `phase3_driver.py` processes concurrently — each one handles one station
end-to-end. Use this when you want to convert several stations in parallel and
measure how much the NFS server / staging SMB can absorb together.

Each station gets its own per-station DB and plan YAML. Within a station, the
driver itself can also parallelize day-jobs via --per-station-workers, so the
effective concurrency is `pool-size * per-station-workers` NFS readers at once.

Run:
  python3 scan/run_phase3_pool.py \\
    --station-dbs /home/unimelb.edu.au/dsand/station_dbs \\
    --plans /tmp/plans_vw \\
    --staging-sds /mnt/seiscomp_staging/seiscomp_archive \\
    --registry metadata/station_registry.yaml \\
    --stations OUTU,HOLS,BRIG,CRJN \\
    --pool-size 4 --per-station-workers 4 [--commit]
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
PHASE3 = os.path.join(HERE, "phase3_driver.py")
VENV_PY_DEFAULT = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"


def run_one_station(args):
    sta, db_path, plan_path, common = args
    t0 = time.time()
    cmd = [
        common["python"], "-u", PHASE3, db_path, plan_path,
        "--registry", common["registry"],
        "--staging-sds", common["staging_sds"],
        "--workers", str(common["per_station_workers"]),
    ]
    if common["commit"]:
        cmd.append("--commit")
    if common.get("limit_days"):
        cmd.extend(["--limit-days", str(common["limit_days"])])
    if common.get("start_date"):
        cmd.extend(["--start-date", common["start_date"]])
    if common.get("end_date"):
        cmd.extend(["--end-date", common["end_date"]])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    return {
        "station": sta,
        "rc": proc.returncode,
        "elapsed_s": elapsed,
        "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station-dbs", required=True,
                    help="dir of per-station DBs (each named <NET>.<STA>.db)")
    ap.add_argument("--plans", required=True,
                    help="dir of per-station plan YAMLs (each named <NET>.<STA>.plan.yaml)")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True)
    ap.add_argument("--stations", default="",
                    help="comma-separated station codes; default = all stations with both "
                         "a DB and a plan in the supplied dirs")
    ap.add_argument("--pool-size", type=int, default=4,
                    help="how many stations to run concurrently (default 4)")
    ap.add_argument("--per-station-workers", type=int, default=4,
                    help="day-pool workers within each station (default 4). "
                         "Effective NFS readers = pool-size * per-station-workers.")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit-days", type=int, default=None)
    ap.add_argument("--start-date")
    ap.add_argument("--end-date")
    ap.add_argument("--python", default=VENV_PY_DEFAULT,
                    help="python interpreter (must have sudspy + obspy + yaml)")
    args = ap.parse_args()

    # Discover station codes from filenames in station-dbs/.
    db_files = {f.split(".")[1]: f for f in os.listdir(args.station_dbs)
                if f.endswith(".db") and f.count(".") >= 2}
    plan_files = {f.split(".")[1]: f for f in os.listdir(args.plans)
                  if f.endswith(".plan.yaml") and f.count(".") >= 2}
    available = sorted(set(db_files) & set(plan_files))
    if args.stations:
        wanted = {s.strip() for s in args.stations.split(",") if s.strip()}
        target = [s for s in available if s in wanted]
        missing = wanted - set(target)
        if missing:
            print(f"WARNING: missing DB+plan for: {sorted(missing)}", flush=True)
    else:
        target = available

    print(f"[pool] {len(target)} stations, pool-size={args.pool_size}, "
          f"per-station-workers={args.per_station_workers}, "
          f"effective NFS readers up to {args.pool_size * args.per_station_workers}",
          flush=True)
    print(f"[pool] commit={args.commit}", flush=True)

    common = {
        "python": args.python,
        "registry": args.registry,
        "staging_sds": args.staging_sds,
        "per_station_workers": args.per_station_workers,
        "commit": args.commit,
        "limit_days": args.limit_days,
        "start_date": args.start_date,
        "end_date": args.end_date,
    }
    jobs = [
        (sta,
         os.path.join(args.station_dbs, db_files[sta]),
         os.path.join(args.plans, plan_files[sta]),
         common)
        for sta in target
    ]

    t0 = time.time()
    completed = 0
    pass_count = 0
    fail_count = 0
    total_elapsed = 0

    with ProcessPoolExecutor(max_workers=args.pool_size) as pool:
        futures = {pool.submit(run_one_station, j): j[0] for j in jobs}
        for fut in as_completed(futures):
            r = fut.result()
            completed += 1
            total_elapsed += r["elapsed_s"]
            tag = "OK " if r["rc"] == 0 else "FAIL"
            print(f"  [{completed:>3}/{len(jobs)}] {tag} {r['station']:8s} "
                  f"elapsed={r['elapsed_s']:>6.0f}s rc={r['rc']}", flush=True)
            if r["rc"] != 0:
                fail_count += 1
                print(f"    STDERR tail:\n{r['stderr_tail']}", flush=True)
            else:
                pass_count += 1

    wall = time.time() - t0
    speedup = total_elapsed / wall if wall > 0 else 0
    print(f"\n[pool] {pass_count} OK / {fail_count} FAIL in {wall:.1f}s wallclock")
    print(f"  cumulative single-station time: {total_elapsed:.1f}s")
    print(f"  effective parallel speedup: {speedup:.2f}x")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

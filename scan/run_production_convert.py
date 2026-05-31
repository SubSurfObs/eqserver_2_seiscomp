#!/usr/bin/env python3
"""run_production_convert.py — drives phase3_driver.py per (station, year),
appends a completion event to convert_done.jsonl on success.

Runs on the **staging VM** (the host that has /mnt/eqserver_archive NFS ro
and /mnt/seiscomp_staging CIFS rw). One of three orchestrator scripts; per
disk_to_sds reply 03 (2026-05-31) the design is **file-queue on shared
mount, no SSH between hosts**:

  staging VM           shared mount                         dev1
  ──────────           ────────────                         ────
  convert.py  ──appends→  convert_done.jsonl  ──tailed by→  promote.py
                          promote_done.jsonl  ◄───appends── promote.py (--commit succeeded)
                          held.jsonl          ◄───appends── promote.py (overrides > 0)
  cleanup.py  ──appends→  cleanup_done.jsonl
              ◄──reads── promote_done.jsonl

Each file has exactly one writer (the host responsible); other hosts only
read it. This avoids CIFS-cross-host append atomicity issues.

Iteration order: **station-by-station outer, year-backwards inner**. For
each (station, year) the script invokes phase3 with
--start-date YYYY-01-01 --end-date YYYY-12-31, captures the run-manifest,
and on success appends one event line to convert_done.jsonl.

Resume: at startup, reads convert_done.jsonl and skips any (sta, year)
tuple already present. Failed runs are NOT appended and will be retried
on restart.

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
import shutil
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


def mirror_plans_to_shared(local_plans_dir: Path, shared_plans_dir: Path,
                           stations: set[str], network: str) -> int:
    """Copy per-station plan YAMLs from the in-git location to the shared
    staging-mount location so the policy_yaml_path recorded in each
    run_manifest resolves from dev1 (apply.py reads it to hash and copy the
    plan into ledger policies/<sha>.yaml). Idempotent: only copies when
    content differs. Plans are ~10 KB each; the whole step is cheap."""
    shared_plans_dir.mkdir(parents=True, exist_ok=True)
    n_copied = 0
    for sta in sorted(stations):
        src = local_plans_dir / f"{network}.{sta}.plan.yaml"
        if not src.exists():
            continue
        dst = shared_plans_dir / f"{network}.{sta}.plan.yaml"
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            continue
        shutil.copy2(src, dst)
        n_copied += 1
    return n_copied


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
    # Per-unit window. Defaults to the full year; the operator may override
    # via --start-date/--end-date (mainly for short smoke tests). When the
    # overrides are supplied they apply identically to every (sta, year) unit
    # the driver visits, so use --year-min/--year-max to restrict the iteration
    # to a single year if you want the overrides to map cleanly.
    start_date = args.start_date or f"{year:04d}-01-01"
    end_date = args.end_date or f"{year:04d}-12-31"
    cmd = [args.python, "-u", str(args.phase3), str(db_path), str(plan_path),
           "--registry", args.registry,
           "--staging-sds", args.staging_sds,
           "--workers", str(args.workers),
           "--commit",
           "--start-date", start_date,
           "--end-date", end_date,
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
    ap.add_argument("--start-date", default=None,
                    help="Override the YYYY-01-01 default start of each "
                         "(sta, year) window. YYYY-MM-DD. Mainly for short "
                         "smoke tests. Combine with --year-min/--year-max "
                         "set to a single year if you want the override to "
                         "map cleanly to one unit.")
    ap.add_argument("--end-date", default=None,
                    help="Override the YYYY-12-31 default end of each "
                         "(sta, year) window. YYYY-MM-DD.")
    ap.add_argument("--station-dbs", default="/home/unimelb.edu.au/dsand/station_dbs")
    ap.add_argument("--plans", default=None,
                    help="Dir of per-station plan YAMLs. Defaults to the "
                         "repo's plans/<network>/ — these are the in-git, "
                         "reviewable, reproducible policy artefacts. Override "
                         "only if you're testing against a one-off plan set.")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True,
                    help="SDS root for the production output. The shared "
                         "staging SDS is /mnt/seiscomp_staging/seiscomp_archive "
                         "(same root disk_to_sds writes into).")
    ap.add_argument("--queue-dir", default=None,
                    help="default: <staging-sds-parent>/eqserver_queue")
    ap.add_argument("--run-manifests-dir", default=None,
                    help="Where convert.py writes the per-(sta, year) "
                         "run_manifest JSONs. MUST be on the shared staging "
                         "mount so promote.py on dev1 can read them. "
                         "Default: <queue-dir>/run_manifests/")
    ap.add_argument("--log-dir", default="/tmp/eqserver_convert_logs",
                    help="Per-unit phase3 logs; can stay local to staging VM.")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--phase3", default=str(DEFAULT_PHASE3))
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    args = ap.parse_args()

    queue = Path(args.queue_dir) if args.queue_dir else \
            queue_dir(Path(args.staging_sds).parent)
    convert_done = queue / "convert_done.jsonl"
    queue.mkdir(parents=True, exist_ok=True)
    # Resolve run_manifests_dir default lazily — keep it on the shared mount
    # alongside the queue files so dev1 can read it via the same path.
    if args.run_manifests_dir is None:
        args.run_manifests_dir = str(queue / "run_manifests")
    # Resolve --plans default: <repo-root>/plans/<network>/
    if args.plans is None:
        args.plans = str(HERE.parent / "plans" / args.network)

    # Resume: which (sta, year) tuples are already in convert_done?
    done_sta_year: set[tuple[str, int]] = set()
    for e in read_all_events(convert_done):
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

    # Mirror the in-git plans into the shared mount so each run_manifest's
    # policy_yaml_path is resolvable from dev1. Without this the manifest
    # would point at the staging VM's local checkout, which dev1 can't see,
    # and apply.py would error with "policy YAML not found" on every unit.
    # After mirroring, switch args.plans so run_convert hands phase3 the
    # shared-mount path (which then lands in the manifest verbatim).
    shared_plans_dir = queue / "plans" / args.network
    work_stations = {sta for sta, _ in work}
    n = mirror_plans_to_shared(Path(args.plans), shared_plans_dir,
                                work_stations, args.network)
    print(f"[convert] mirrored {n} plan(s) to {shared_plans_dir}", flush=True)
    args.plans = str(shared_plans_dir)

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
            append_event(convert_done, event)
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

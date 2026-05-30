#!/usr/bin/env python3
"""Random-week stress harness: per VW station, pick N non-overlapping weeks
from the 2023-2025 Pass-1 pool and run phase3 on those exact dates.

Targets (see PROGRESS.md):
  - Exercise all 26 in-window VW stations across all 4 recorder cohorts
    (echopro / gecko / minimus / RT130-via-gecko)
  - Surface tail bugs at scale (~1,414 day-jobs in the round-1 default)
  - Aggregate throughput for production-tuning estimates
  - Auto-pause on quota errors (Mediaflux EDQUOT / ENOSPC) — designed for
    the over-quota situation where the host is lenient but not infinite

Resumable: state file records per-station completion; re-running skips done.
Per-station seed makes individual stations re-rollable without affecting peers.

Run (backgrounded with logging):
  nohup python3 -u scan/stress_random_weeks.py \
      --registry metadata/station_registry.yaml \
      --staging-sds /mnt/seiscomp_staging/stress_round1 \
      --workers 4 --target-weeks 8 \
      > /tmp/stress_round1.log 2>&1 &
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"
DEFAULT_PHASE3 = os.path.join(HERE, "phase3_driver.py")

WIN_START = date(2023, 1, 1)
WIN_END = date(2025, 12, 31)
SUPPORTED = {"echopro", "gecko", "minimus", "reftek_rt130"}

# Strings we treat as "Mediaflux pushed back, stop dispatching." Both POSIX
# error codes and the human-readable equivalents the CIFS driver surfaces.
QUOTA_SIGNALS = (
    "EDQUOT", "ENOSPC",
    "Disk quota exceeded",
    "No space left on device",
    "Quota exceeded",
)


def pick_weeks(plan, target_weeks, seed):
    """Uniform random selection of `target_weeks` non-overlapping 7-day blocks
    from the station's eligible 2023-2025 window (within SUPPORTED epochs).
    Returns sorted list of week-start dates."""
    rng = random.Random(seed)
    ranges = []
    for ep in plan.get("epochs", []):
        if ep.get("recorder") not in SUPPORTED:
            continue
        s = max(date.fromisoformat(ep["start"]), WIN_START)
        e = min(date.fromisoformat(ep["end"]), WIN_END)
        if e >= s:
            ranges.append((s, e))
    candidates = []
    for s, e in ranges:
        d = s
        while d + timedelta(days=6) <= e:
            candidates.append(d)
            d += timedelta(days=1)
    rng.shuffle(candidates)
    picked = []
    for c in candidates:
        if all(abs((c - p).days) >= 7 for p in picked):
            picked.append(c)
            if len(picked) == target_weeks:
                break
    picked.sort()
    return picked


def is_quota_error(stdout, stderr):
    blob = (stdout or "") + (stderr or "")
    return any(sig in blob for sig in QUOTA_SIGNALS)


def parse_phase3_summary(stdout):
    """Pull the tail-line numbers phase3 prints: days_processed, bytes, status counts."""
    days_processed = 0
    bytes_written = 0
    status_counts = {}
    for ln in stdout.splitlines():
        m = re.search(r"days processed:\s*(\d+)", ln)
        if m:
            days_processed = int(m.group(1))
        m = re.search(r"total bytes written:\s*([\d,]+)", ln)
        if m:
            bytes_written = int(m.group(1).replace(",", ""))
        m = re.search(r"status counts:\s*(\{[^}]*\})", ln)
        if m:
            try:
                status_counts = eval(m.group(1))  # phase3 prints a python dict literal
            except Exception:
                pass
    return days_processed, bytes_written, status_counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", default="/tmp/plans_vw")
    ap.add_argument("--station-dbs",
                    default="/home/unimelb.edu.au/dsand/station_dbs")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True,
                    help="SDS root for the stress output (recommend a dedicated dir, "
                         "e.g. /mnt/seiscomp_staging/stress_round1)")
    ap.add_argument("--target-weeks", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=4,
                    help="phase3 per-day worker pool size")
    ap.add_argument("--state-file", default="/tmp/stress_state.json")
    ap.add_argument("--results-file", default="/tmp/stress_results.jsonl")
    ap.add_argument("--dates-dir", default="/tmp/stress_dates",
                    help="where per-station dates files are written")
    ap.add_argument("--phase3", default=DEFAULT_PHASE3)
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    ap.add_argument("--dry-run", action="store_true",
                    help="don't pass --commit to phase3 (no SDS writes)")
    ap.add_argument("--stations", default="",
                    help="comma-separated subset to run (default: all VW)")
    args = ap.parse_args()

    import yaml  # imported here so --help doesn't require pyyaml

    os.makedirs(args.dates_dir, exist_ok=True)

    # Resume state
    if os.path.exists(args.state_file):
        state = json.load(open(args.state_file))
    else:
        state = {"started_at": time.time(), "done": {}, "halted_reason": None,
                 "seed": args.seed, "target_weeks": args.target_weeks}

    # Build the pool
    wanted = {s.strip() for s in args.stations.split(",") if s.strip()}
    pool = []
    for p in sorted(glob.glob(os.path.join(args.plans, "VW.*.plan.yaml"))):
        plan = yaml.safe_load(open(p))
        sta = plan["station"]
        if wanted and sta not in wanted:
            continue
        db = os.path.join(args.station_dbs, f"VW.{sta}.db")
        if not os.path.exists(db):
            print(f"[stress] {sta} skipped: no DB at {db}", flush=True)
            continue
        per_sta_seed = args.seed + sum(ord(c) for c in sta)
        picks = pick_weeks(plan, args.target_weeks, per_sta_seed)
        if not picks:
            print(f"[stress] {sta} skipped: no eligible weeks in window", flush=True)
            continue
        pool.append((sta, db, p, picks))

    total_weeks = sum(len(x[3]) for x in pool)
    total_days = total_weeks * 7
    print(f"[stress] {len(pool)} stations | {total_weeks} weeks | {total_days} day-jobs "
          f"| ~{total_days * 75 / 1024:.1f} GB est | workers={args.workers} "
          f"| seed={args.seed} | commit={not args.dry_run}", flush=True)
    print(f"[stress] state: {args.state_file}  results: {args.results_file}", flush=True)
    print(f"[stress] {len(state['done'])} stations already done (will skip)", flush=True)

    overall_t0 = time.time()
    halted = False
    for i, (sta, db, plan_p, picks) in enumerate(pool, 1):
        if halted:
            break
        if sta in state["done"]:
            print(f"[{i:>2}/{len(pool)}] {sta:6} SKIP (already done)", flush=True)
            continue

        # Materialise dates file for this station
        dates_file = os.path.join(args.dates_dir, f"VW_{sta}.dates")
        with open(dates_file, "w") as f:
            f.write(f"# {sta}: {len(picks)} weeks (seed={args.seed})\n")
            for w in picks:
                for d_off in range(7):
                    f.write((w + timedelta(days=d_off)).isoformat() + "\n")
        n_days_planned = 7 * len(picks)

        cmd = [args.python, "-u", args.phase3, db, plan_p,
               "--registry", args.registry,
               "--staging-sds", args.staging_sds,
               "--workers", str(args.workers),
               "--dates-file", dates_file]
        if not args.dry_run:
            cmd.append("--commit")

        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - t0

        days_proc, bytes_w, status_counts = parse_phase3_summary(proc.stdout)
        quota = is_quota_error(proc.stdout, proc.stderr)

        result = {
            "station": sta, "rc": proc.returncode,
            "weeks": [w.isoformat() for w in picks],
            "n_weeks": len(picks), "n_days_planned": n_days_planned,
            "days_processed": days_proc,
            "bytes_written": bytes_w,
            "status_counts": status_counts,
            "elapsed_s": round(elapsed, 1),
            "throughput_days_per_s": round(days_proc / elapsed, 3) if elapsed > 0 else 0,
            "quota_error": quota,
            "stderr_tail": proc.stderr[-500:] if proc.returncode != 0 else "",
        }
        state["done"][sta] = result
        with open(args.results_file, "a") as f:
            f.write(json.dumps(result) + "\n")
        with open(args.state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

        tag = "QUOTA" if quota else ("OK" if proc.returncode == 0 else "FAIL")
        print(f"[{i:>2}/{len(pool)}] {sta:6} {tag:5} {len(picks)}wk={n_days_planned}d "
              f"proc={days_proc:>3} bytes={bytes_w/1024/1024:>6.0f}MB "
              f"{elapsed:>5.0f}s ({result['throughput_days_per_s']:.2f} d/s) "
              f"counts={status_counts}", flush=True)

        if quota:
            print(f"[stress] QUOTA HIT — halting. Resume by re-running with the same "
                  f"--state-file once the quota lifts.", flush=True)
            state["halted_reason"] = "quota_error"
            with open(args.state_file, "w") as f:
                json.dump(state, f, indent=2, default=str)
            halted = True
        elif proc.returncode != 0:
            print(f"[stress] phase3 returned rc={proc.returncode}; continuing. stderr tail:\n"
                  f"{proc.stderr[-400:]}", flush=True)

    wall = time.time() - overall_t0
    n_ok = sum(1 for r in state["done"].values() if r["rc"] == 0 and not r["quota_error"])
    n_fail = sum(1 for r in state["done"].values() if r["rc"] != 0)
    n_quota = sum(1 for r in state["done"].values() if r["quota_error"])
    tot_bytes = sum(r["bytes_written"] for r in state["done"].values())
    tot_days = sum(r["days_processed"] for r in state["done"].values())
    print(f"\n[stress] run finished in {wall:.0f}s ({wall/3600:.2f}h). "
          f"ok={n_ok} fail={n_fail} quota={n_quota} | "
          f"days={tot_days} bytes={tot_bytes/1024**3:.2f} GB")
    return 0 if (n_fail == 0 and n_quota == 0) else 1


if __name__ == "__main__":
    sys.exit(main())

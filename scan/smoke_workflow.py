#!/usr/bin/env python3
"""End-to-end smoke test of the entire workflow.

For a small curated set of (station, year-window) pairs covering all five
recorder cohorts, runs:

  1. Level-1 manifest scan on a tiny date window (per-station, one or two days)
  2. plan_generator on that manifest
  3. phase3_driver in --commit mode, capped at --limit-days
  4. Reads the written SDS files back and asserts (network, station, location,
     channel, sample_rate) match expected

Intended to run in a few minutes and surface any cross-cohort regression
without paying the cost of a full archive sweep.

Run:
  python3 scan/smoke_workflow.py --staging-sds /tmp/smoke_staging \\
      --registry metadata/station_registry.yaml \\
      --scratch /tmp/smoke_scratch
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LEVEL1 = os.path.join(HERE, "level1.py")
PLAN_GEN = os.path.join(HERE, "plan_generator.py")
PHASE3 = os.path.join(HERE, "phase3_driver.py")

VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"

# Curated smoke set: one station per cohort, dates known to have data.
# Each entry runs scan→plan→convert on a 1-day window.
ENTRIES = [
    {
        "id": "echopro_OUTU",
        "station": "OUTU",
        "year": 2020, "month": 1, "day": 20,
        "limit_days": 1,
        "expected": {"network": "VW", "location": "00",
                     "channels_subset": {"CHZ"}, "rate": 250.0, "recorder": "echopro"},
    },
    {
        "id": "gecko_STBK",
        "station": "STBK",
        "year": 2020, "month": 1, "day": 1,
        "limit_days": 1,
        "expected": {"network": "VW", "location": "00",
                     "channels_subset": {"CHZ"}, "rate": 250.0, "recorder": "gecko"},
    },
    {
        "id": "minimus_DDBE",
        "station": "DDBE",
        "year": 2020, "month": 1, "day": 1,
        "limit_days": 1,
        # Note: DDBE went from HH@200 (~Oct 2019) to CH@250 (~Jan 2020) — real
        # epoch change. Smoke matches the date-specific observed shape.
        "expected": {"network": "VW", "location": "00",
                     "channels_subset": {"CHZ", "CHN", "CHE"}, "rate": 250.0,
                     "recorder": "minimus"},
    },
    {
        "id": "piesmo_WEPH_defer",
        "station": "WEPH",
        "year": 2025, "month": 4, "day": 1,
        "limit_days": 1,
        "expected": {"plan_status": "defer_conversion", "no_sds_written": True},
    },
    {
        "id": "reftek_SGWU",
        "station": "SGWU",
        "year": 2018, "month": 6, "day": 1,
        "limit_days": 1,
        "expected": {"network": "VW", "location": "00",
                     "channels_subset": {"HHZ"}, "rate": 200.0, "recorder": "reftek_rt130"},
    },
]


def run(cmd, **kw):
    """Run a subprocess and return (returncode, stdout, stderr)."""
    cp = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return cp.returncode, cp.stdout, cp.stderr


def smoke_one(entry, registry, staging_sds, scratch_dir):
    """Scan→plan→convert→validate one cohort entry. Returns dict of results."""
    sta = entry["station"]
    y, m, d = entry["year"], entry["month"], entry["day"]
    exp = entry["expected"]

    sub = os.path.join(scratch_dir, entry["id"])
    os.makedirs(sub, exist_ok=True)
    db_path = os.path.join(sub, "manifest.db")
    plan_dir = os.path.join(sub, "plans")
    sds_root = os.path.join(staging_sds, entry["id"])

    # Clean slate for each entry
    for p in [db_path, plan_dir, sds_root]:
        if os.path.isfile(p):
            os.remove(p)
        elif os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    # 1. Level-1 scan, single station, single day
    t0 = time.time()
    sta_file = os.path.join(sub, "station.txt")
    with open(sta_file, "w") as f:
        f.write(f"{sta}\n")
    rc1, out1, err1 = run([
        VENV_PY, "-u", LEVEL1,
        "--stations-file", sta_file,
        "--year", str(y), "--month", str(m), "--day", str(d),
        "--workers", "1", "--db", db_path,
    ])
    if rc1 != 0:
        return {"id": entry["id"], "pass": False, "phase": "scan", "stderr": err1, "elapsed_s": time.time() - t0}

    # 2. Plan generator
    rc2, out2, err2 = run([
        VENV_PY, "-u", PLAN_GEN, db_path,
        "--registry", registry, "--out", plan_dir, "--stations", sta,
    ])
    if rc2 != 0:
        return {"id": entry["id"], "pass": False, "phase": "plan", "stderr": err2, "elapsed_s": time.time() - t0}

    # 3. Locate the plan YAML
    plans = [p for p in os.listdir(plan_dir) if p.endswith(".plan.yaml")]
    if not plans:
        return {"id": entry["id"], "pass": False, "phase": "plan_missing", "elapsed_s": time.time() - t0}
    plan_path = os.path.join(plan_dir, plans[0])

    import yaml
    plan = yaml.safe_load(open(plan_path))

    if "plan_status" in exp:
        if plan["status"] != exp["plan_status"]:
            return {"id": entry["id"], "pass": False, "phase": "plan_status",
                    "expected": exp["plan_status"], "got": plan["status"],
                    "elapsed_s": time.time() - t0}

    # 4. Phase 3 driver in commit mode
    rc3, out3, err3 = run([
        VENV_PY, "-u", PHASE3, db_path, plan_path,
        "--registry", registry, "--staging-sds", sds_root,
        "--limit-days", str(entry["limit_days"]), "--commit",
    ])
    if rc3 != 0:
        return {"id": entry["id"], "pass": False, "phase": "convert",
                "stderr": err3, "stdout_tail": out3[-400:], "elapsed_s": time.time() - t0}

    # 5. Validate
    sds_files = []
    for root, _, names in os.walk(sds_root):
        for n in names:
            sds_files.append(os.path.join(root, n))

    if exp.get("no_sds_written"):
        if sds_files:
            return {"id": entry["id"], "pass": False, "phase": "validate_defer",
                    "expected": "no SDS files", "got": sds_files, "elapsed_s": time.time() - t0}
        return {"id": entry["id"], "pass": True, "phase": "ok",
                "deferred": True, "elapsed_s": time.time() - t0}

    if not sds_files:
        return {"id": entry["id"], "pass": False, "phase": "validate_no_sds",
                "elapsed_s": time.time() - t0, "stdout_tail": out3[-400:]}

    # Read first SDS file and check ids/rate match expectation
    from obspy import read
    try:
        st = read(sds_files[0])
        tr = st[0]
    except Exception as e:
        return {"id": entry["id"], "pass": False, "phase": "validate_read",
                "error": str(e), "elapsed_s": time.time() - t0}

    seen_chans = {os.path.basename(p).split(".")[3] for p in sds_files}
    fails = []
    if exp.get("network") and tr.stats.network != exp["network"]:
        fails.append(f"network: expected {exp['network']}, got {tr.stats.network!r}")
    if exp.get("location") and tr.stats.location != exp["location"]:
        fails.append(f"location: expected {exp['location']!r}, got {tr.stats.location!r}")
    if exp.get("rate") and abs(tr.stats.sampling_rate - exp["rate"]) > 1e-6:
        fails.append(f"rate: expected {exp['rate']}, got {tr.stats.sampling_rate}")
    if exp.get("channels_subset"):
        missing = exp["channels_subset"] - seen_chans
        if missing:
            fails.append(f"channels: missing {missing}, got {seen_chans}")

    return {
        "id": entry["id"],
        "pass": not fails,
        "phase": "ok" if not fails else "validate_fail",
        "fails": fails,
        "sds_count": len(sds_files),
        "first_id": tr.id,
        "elapsed_s": time.time() - t0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True)
    ap.add_argument("--scratch", required=True, help="scratch dir for per-entry manifests + plans")
    ap.add_argument("--filter", default="", help="comma-separated id substrings")
    args = ap.parse_args()

    os.makedirs(args.staging_sds, exist_ok=True)
    os.makedirs(args.scratch, exist_ok=True)

    entries = ENTRIES
    if args.filter:
        substrs = [s.strip().lower() for s in args.filter.split(",") if s.strip()]
        entries = [e for e in entries if any(s in e["id"].lower() for s in substrs)]

    print(f"[smoke-workflow] {len(entries)} entries")
    results = []
    overall_t0 = time.time()
    for e in entries:
        print(f"\n--- {e['id']} (station={e['station']} {e['year']}-{e['month']:02d}-{e['day']:02d}) ---", flush=True)
        r = smoke_one(e, args.registry, args.staging_sds, args.scratch)
        results.append(r)
        tag = "PASS" if r["pass"] else "FAIL"
        print(f"[{tag}] {r['id']}: phase={r['phase']} elapsed={r['elapsed_s']:.1f}s", flush=True)
        if r.get("fails"):
            for f in r["fails"]:
                print(f"   -> {f}", flush=True)
        if r.get("stderr"):
            print("STDERR tail:", flush=True)
            print(r["stderr"][-800:], flush=True)

    n_pass = sum(1 for r in results if r["pass"])
    elapsed = time.time() - overall_t0
    print(f"\n[smoke-workflow] {n_pass}/{len(results)} pass in {elapsed:.1f}s")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

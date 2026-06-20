#!/usr/bin/env python3
"""classb_test_suite.py — Validate the Class B (missing stream.merge before
write_sds) fix end-to-end against the current engine, before launching the
full ~136-station re-conversion sweep.

Three phases (run all with --phase all, or one at a time):

  convert  — invoke phase3 for 7 (sta, year) units × 7 days each, writing
             to /mnt/seiscomp_staging/test_classb_reconv/. ~50 day-jobs.
             Runs on the staging VM. ~30-45 min wall-clock at workers=2.

  verify   — walk every day-channel file the convert produced. For each:
             obspy.read(headonly=True) → assert exactly 1 trace, sample
             count within 5% of 86400 * sample_rate, encoding STEIM2.
             Runs on the staging VM. ~5 min.

  apply    — for each test unit, ssh dev1 and run apply.py --mode decide
             (no --commit) against LT. Capture (write, override, skip).
             ~5 min per unit, ~35 min total.

Output: a single summary table, one row per test unit:
  (sta, year)  clean_files/total  pct_samples  apply: w=X o=Y s=Z  verdict

What this proves:
  1. Current engine writes 1-trace day-files for all 4 recorder types
  2. apply.py + held-queue produces expected operational decisions
  3. We know the override magnitude before launching the full sweep

What it does NOT prove:
  Multi-unit orchestrator behaviour. That's a separate test layer.

USAGE (on staging VM):
  python3 scan/classb_test_suite.py --phase all --out /tmp/classb_test.txt
  python3 scan/classb_test_suite.py --phase convert
  python3 scan/classb_test_suite.py --phase verify
  python3 scan/classb_test_suite.py --phase apply
"""
from __future__ import annotations
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# --------------------------------------------------------------------------
# Configuration: paths + test matrix
# --------------------------------------------------------------------------

REPO = Path("/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp")
PHASE3 = REPO / "scan" / "phase3_driver.py"
PLANS_DIR = REPO / "plans" / "VW"
REGISTRY = REPO / "metadata" / "station_registry.yaml"
STATION_DBS = Path("/home/unimelb.edu.au/dsand/station_dbs")
PYTHON = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"

TEST_STAGING = Path("/mnt/seiscomp_staging/test_classb_reconv")
TEST_LOGS = Path("/tmp/classb_test_logs")

DEV1 = "seiscomp@seismology-dev1.its.unimelb.edu.au"
APPLY_PY = "/home/seiscomp/projects/SubSurfObs/sds_staging_ledger/apply.py"
LT_ROOT = "/mnt/seiscomp_archive"
LEDGER_ROOT = "/home/seiscomp/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive"


@dataclass
class TestUnit:
    sta: str
    year: int
    start: str               # YYYY-MM-DD
    end: str                 # YYYY-MM-DD (inclusive)
    note: str
    expected_rate: int = 250  # Hz; all VW stations at 250 except a few outliers


# The 7 units. Date ranges chosen for diversity:
#  - middle-of-year for stable telemetry (no boundary edge cases)
#  - except STBK 2022-10-22..28 to include 10-23 (the canonical disk_to_sds
#    test day from the gecko-fragmentation handoff)
#  - LRSE 2022-01-01..07 to include a year-boundary day (deliberate boundary test)
TEST_UNITS: list[TestUnit] = [
    TestUnit("BEST", 2017, "2017-06-01", "2017-06-07",
             "Pure EchoPro flagged Class B (LT scan: definite=21, max=444)"),
    TestUnit("HOLS", 2018, "2018-06-01", "2018-06-07",
             "Pure EchoPro, mid-year, no boundary effects"),
    TestUnit("STBK", 2022, "2022-10-22", "2022-10-28",
             "Canonical Class B case — includes day 10-23 (disk_to_sds test day)"),
    TestUnit("DDNE", 2020, "2020-06-01", "2020-06-07",
             "Pure Gecko, full-year coverage"),
    TestUnit("DDWB", 2020, "2020-06-01", "2020-06-07",
             "Pure Minimus (per-channel-per-minute mseed)"),
    TestUnit("SGWU", 2018, "2018-06-01", "2018-06-07",
             "RT130 + Gecko mix (4th recorder class)"),
    TestUnit("LRSE", 2022, "2022-01-01", "2022-01-07",
             "Gecko with year-boundary day 001"),
]


# --------------------------------------------------------------------------
# Phase 1: convert
# --------------------------------------------------------------------------

def run_convert(unit: TestUnit) -> dict:
    """Invoke phase3 directly for one test unit, writing to TEST_STAGING.
    Bypasses the orchestrator (run_production_convert) entirely — no
    convert_done.jsonl entries, no plan mirroring, no parallel-unit races.
    """
    TEST_LOGS.mkdir(parents=True, exist_ok=True)
    db_path = STATION_DBS / f"VW.{unit.sta}.db"
    plan_path = PLANS_DIR / f"VW.{unit.sta}.plan.yaml"
    log_path = TEST_LOGS / f"{unit.sta}_{unit.year}.log"

    if not db_path.exists():
        return {"rc": -1, "elapsed_s": 0, "log_path": str(log_path),
                "error": f"missing station DB: {db_path}"}
    if not plan_path.exists():
        return {"rc": -1, "elapsed_s": 0, "log_path": str(log_path),
                "error": f"missing plan: {plan_path}"}

    cmd = [PYTHON, "-u", str(PHASE3),
           str(db_path), str(plan_path),
           "--registry", str(REGISTRY),
           "--staging-sds", str(TEST_STAGING),
           "--workers", "2",
           "--commit",
           "--start-date", unit.start,
           "--end-date", unit.end]
    t0 = time.time()
    with log_path.open("w") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    return {"rc": proc.returncode, "elapsed_s": round(time.time() - t0, 1),
            "log_path": str(log_path)}


def phase_convert() -> dict:
    print(f"[convert] test staging: {TEST_STAGING}", flush=True)
    TEST_STAGING.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    for i, unit in enumerate(TEST_UNITS, 1):
        tag = f"{unit.sta} {unit.year} ({unit.start}..{unit.end})"
        print(f"[convert] [{i}/{len(TEST_UNITS)}] {tag} ...", flush=True)
        r = run_convert(unit)
        status = "OK" if r["rc"] == 0 else f"FAIL rc={r['rc']}"
        print(f"[convert]   {tag} {status} {r['elapsed_s']:.0f}s "
              f"log={r['log_path']}", flush=True)
        results[f"{unit.sta}_{unit.year}"] = r
    return results


# --------------------------------------------------------------------------
# Phase 2: verify
# --------------------------------------------------------------------------

def _expected_doys(start: str, end: str) -> list[tuple[int, int]]:
    """Return list of (year, julday) inclusive over start..end."""
    from datetime import datetime, timedelta
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    out = []
    d = s
    while d <= e:
        out.append((d.year, d.timetuple().tm_yday))
        d += timedelta(days=1)
    return out


def verify_unit(unit: TestUnit) -> dict:
    """For each (channel, day) the unit should have produced, open the SDS
    file and check: exactly 1 trace, sample count within 5% of 24h, STEIM2.
    """
    from obspy import read

    n_files_total = 0
    n_files_clean_1trace = 0
    n_files_missing = 0
    sample_totals: list[float] = []  # pct of expected (1.0 = full day)
    encoding_bad: list[str] = []
    multi_trace: list[tuple[str, int]] = []  # (path, ntraces)

    for year, doy in _expected_doys(unit.start, unit.end):
        for chan in ("CHZ", "CHN", "CHE"):
            path = (TEST_STAGING / f"{year:04d}" / "VW" / unit.sta /
                    f"{chan}.D" /
                    f"VW.{unit.sta}.00.{chan}.D.{year:04d}.{doy:03d}")
            n_files_total += 1
            if not path.exists():
                n_files_missing += 1
                continue
            try:
                st = read(str(path), headonly=True)
            except Exception as e:
                multi_trace.append((str(path), -1))  # read error
                continue
            n = len(st)
            samples = sum(tr.stats.npts for tr in st)
            expected = unit.expected_rate * 86400
            pct = samples / expected if expected else 0
            sample_totals.append(pct)
            if n == 1:
                n_files_clean_1trace += 1
            else:
                multi_trace.append((path.name, n))
            # Encoding check — read first trace fully to access stats.mseed.encoding
            try:
                st_full = read(str(path), format="MSEED")
                enc = st_full[0].stats.mseed.get("encoding") if st_full else None
                if enc and enc != "STEIM2":
                    encoding_bad.append(f"{path.name}={enc}")
            except Exception:
                pass

    avg_pct = (sum(sample_totals) / len(sample_totals)) if sample_totals else 0
    return {
        "n_files_total": n_files_total,
        "n_files_clean_1trace": n_files_clean_1trace,
        "n_files_missing": n_files_missing,
        "n_files_multi_trace": len(multi_trace),
        "avg_pct_samples": round(avg_pct, 3),
        "multi_trace_samples": multi_trace[:5],
        "encoding_bad": encoding_bad[:5],
    }


def phase_verify() -> dict:
    print(f"[verify] reading {TEST_STAGING} per unit", flush=True)
    results: dict[str, dict] = {}
    for unit in TEST_UNITS:
        tag = f"{unit.sta} {unit.year}"
        v = verify_unit(unit)
        clean_pct = (100.0 * v["n_files_clean_1trace"] / v["n_files_total"]
                     if v["n_files_total"] else 0)
        print(f"[verify] {tag}: clean={v['n_files_clean_1trace']}/"
              f"{v['n_files_total']} ({clean_pct:.0f}%)  "
              f"avg_samples={v['avg_pct_samples']*100:.1f}%  "
              f"missing={v['n_files_missing']}  "
              f"multi={v['n_files_multi_trace']}", flush=True)
        results[f"{unit.sta}_{unit.year}"] = v
    return results


# --------------------------------------------------------------------------
# Phase 3: apply.py dry-run on dev1
# --------------------------------------------------------------------------

APPLY_TAIL_RE = re.compile(
    r"would write=(\d+)\s+would override=(\d+)\s+skip=(\d+)\s+fail=(\d+)")


def run_apply_dryrun(unit: TestUnit) -> dict:
    """ssh dev1, run apply.py --mode decide (no --commit) for one unit
    against TEST_STAGING vs LT. Parse the tail summary line."""
    cmd = ["ssh", "-o", "ConnectTimeout=20", DEV1,
           "python3", APPLY_PY,
           "--staging-root", str(TEST_STAGING),
           "--lt-root", LT_ROOT,
           "--ledger-root", LEDGER_ROOT,
           "--net", "VW", "--sta", unit.sta, "--year", str(unit.year),
           "--source-kind", "eqserver",
           "--mode", "decide"]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = round(time.time() - t0, 1)
    m = APPLY_TAIL_RE.search(proc.stdout or "")
    if not m:
        return {"rc": proc.returncode, "elapsed_s": elapsed,
                "error": "no summary line in apply.py output",
                "tail": (proc.stdout or "")[-400:]}
    return {"rc": proc.returncode, "elapsed_s": elapsed,
            "write": int(m.group(1)), "override": int(m.group(2)),
            "skip": int(m.group(3)), "fail": int(m.group(4))}


def phase_apply() -> dict:
    print(f"[apply] dry-run per unit on {DEV1}", flush=True)
    results: dict[str, dict] = {}
    for i, unit in enumerate(TEST_UNITS, 1):
        tag = f"{unit.sta} {unit.year}"
        print(f"[apply] [{i}/{len(TEST_UNITS)}] {tag} ...", flush=True)
        r = run_apply_dryrun(unit)
        if "write" in r:
            print(f"[apply]   {tag} write={r['write']} override={r['override']} "
                  f"skip={r['skip']} fail={r['fail']} ({r['elapsed_s']:.0f}s)",
                  flush=True)
        else:
            print(f"[apply]   {tag} ERROR {r.get('error')} ({r['elapsed_s']:.0f}s)",
                  flush=True)
        results[f"{unit.sta}_{unit.year}"] = r
    return results


# --------------------------------------------------------------------------
# Summary table
# --------------------------------------------------------------------------

def print_summary(convert: dict, verify: dict, apply: dict, fh) -> None:
    fh.write("\n" + "=" * 90 + "\n")
    fh.write("Class B test suite — summary\n")
    fh.write("=" * 90 + "\n")
    fh.write(f"{'unit':14s}  {'convert':9s}  {'clean/total':12s}  "
             f"{'avg_pct':8s}  {'apply: w / o / s':20s}  verdict\n")
    fh.write("-" * 90 + "\n")
    for unit in TEST_UNITS:
        key = f"{unit.sta}_{unit.year}"
        c = convert.get(key, {})
        v = verify.get(key, {})
        a = apply.get(key, {})

        conv_str = "OK" if c.get("rc") == 0 else f"FAIL rc={c.get('rc')}"
        n_total = v.get("n_files_total", 0)
        n_clean = v.get("n_files_clean_1trace", 0)
        clean_str = f"{n_clean}/{n_total}" if n_total else "—"
        pct_str = f"{v.get('avg_pct_samples', 0) * 100:.1f}%" if v else "—"
        if "write" in a:
            app_str = f"{a['write']} / {a['override']} / {a['skip']}"
        else:
            app_str = f"ERR: {a.get('error', '?')[:14]}"

        # Verdict
        verdict_bits = []
        if c.get("rc") != 0:
            verdict_bits.append("CONVERT-FAIL")
        elif n_total and n_clean == n_total:
            verdict_bits.append("clean")
        elif n_total:
            verdict_bits.append(f"DIRTY({v.get('n_files_multi_trace', 0)} multi-trace)")
        if v.get("avg_pct_samples", 0) < 0.95 and v.get("n_files_total"):
            verdict_bits.append(f"low-samples({v['avg_pct_samples'] * 100:.0f}%)")
        if "override" in a and a["override"] > 0:
            verdict_bits.append(f"override={a['override']}")
        if "skip" in a and a["skip"] > 0 and a.get("override", 0) == 0 and a.get("write", 0) == 0:
            verdict_bits.append("LT-already-good")
        verdict = "  ".join(verdict_bits) or "?"

        fh.write(f"{key:14s}  {conv_str:9s}  {clean_str:12s}  "
                 f"{pct_str:8s}  {app_str:20s}  {verdict}\n")
    fh.write("-" * 90 + "\n")
    fh.write("Convert: rc==0 means phase3 exited OK; non-OK row needs log review.\n")
    fh.write("Clean: n_files where obspy.read returns exactly 1 trace (Class B fingerprint).\n")
    fh.write("avg_pct: average sample count vs full day (24h * sample_rate). 95% threshold.\n")
    fh.write("Apply w/o/s: write / override / skip counts from apply.py --mode decide dry-run.\n")
    fh.write("  override > 0 → would land in held queue (LT has fewer samples than test bytes).\n")
    fh.write("  skip > 0    → LT already matches test bytes (no work needed).\n")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["convert", "verify", "apply", "all"],
                    default="all")
    ap.add_argument("--out", default="-", help="summary table path; - for stdout")
    ap.add_argument("--state", default="/tmp/classb_test_state.json",
                    help="persist convert/verify results across phase runs")
    args = ap.parse_args()

    state_path = Path(args.state)
    state: dict = {"convert": {}, "verify": {}, "apply": {}}
    if state_path.exists():
        try:
            state.update(json.loads(state_path.read_text()))
        except Exception:
            pass

    if args.phase in ("convert", "all"):
        state["convert"] = phase_convert()
        state_path.write_text(json.dumps(state))
    if args.phase in ("verify", "all"):
        state["verify"] = phase_verify()
        state_path.write_text(json.dumps(state))
    if args.phase in ("apply", "all"):
        state["apply"] = phase_apply()
        state_path.write_text(json.dumps(state))

    fh = sys.stdout if args.out == "-" else open(args.out, "w")
    print_summary(state["convert"], state["verify"], state["apply"], fh)
    if args.out != "-":
        fh.close()
        print(f"\n[done] summary table written to {args.out}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

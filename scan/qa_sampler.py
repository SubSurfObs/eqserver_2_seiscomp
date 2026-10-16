#!/usr/bin/env python3
"""qa_sampler — build the stratified random work queue for the QA campaign.

Universe = v1 promoted units (promote_done.jsonl, action=promoted) × their
days-with-data (from the Level-1 station DBs). Strata:

  uniform      seeded uniform random over all (unit, day)
  boundary     Jan 1 + Dec 31 of every unit (year-boundary stitching)
  class2       Class-2 candidate stations (disk CHZ-only eras), 2017-2023
  pre_c        units converted before the Option-C midnight fix landed
               (convert ts < 2026-06-02T10:20 AEST ~= 00:20Z)
  qc_flagged   days with status=qc_flagged in the v1 run manifests

Output: jsonl work queue {net, sta, date, stratum}, deduped, interleaved
round-robin across strata so every stratum gets early coverage. Reproducible
via --seed. Read-only everywhere except --out.
"""

import argparse
import json
import random
import sqlite3
import sys
from datetime import date as date_cls
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_verify import find_db, DEFAULT_DB_DIRS

CLASS2_STATIONS = {"WLSH", "WDSD", "BRTH", "DDSW", "DDNE", "DDWK", "FORG", "BEST"}
CLASS2_YEARS = range(2017, 2024)
PRE_C_CUTOFF = "2026-06-02T00:20"  # UTC ~ 10:20 AEST, Option-C landing


def promoted_units(queue_dir: Path) -> list[tuple]:
    units, seen = [], set()
    with open(queue_dir / "promote_done.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("action") != "promoted":
                continue
            k = (d["net"], d["sta"], d["year"])
            if k not in seen:
                seen.add(k)
                units.append(k)
    return units


def first_convert_ts(queue_dir: Path) -> dict:
    out = {}
    with open(queue_dir / "convert_done.jsonl") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            k = (d["net"], d["sta"], d["year"])
            ts = d.get("ts", "")
            if k not in out or ts < out[k]:
                out[k] = ts
    return out


def days_by_year(db_dirs: list, net: str, sta: str) -> dict[int, list]:
    """One full-table scan per STATION: {year: [date, ...]}."""
    p = find_db(db_dirs, net, sta)
    if p is None:
        raise FileNotFoundError(f"no usable {net}.{sta}.db on search path")
    db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT DISTINCT file_year, file_month, file_day FROM files "
        "WHERE role='waveform' AND station_mismatch=0 "
        "AND exclude_reason IS NULL")
    out: dict[int, list] = {}
    for y, m, d in rows:
        try:
            out.setdefault(y, []).append(date_cls(y, m, d))
        except (ValueError, TypeError):
            continue
    db.close()
    return {y: sorted(v) for y, v in out.items()}


def qc_flagged_days(queue_dir: Path) -> list[tuple]:
    out = []
    for mf in sorted((queue_dir / "run_manifests").glob("*.json")):
        try:
            d = json.loads(mf.read_text())
        except Exception:
            continue
        eq = d.get("eqserver", d)
        pds = eq.get("per_date_status") or []
        if isinstance(pds, dict):
            pds = [{"date": k, "status": v} for k, v in pds.items()]
        for item in pds:
            if isinstance(item, dict) and item.get("status") == "qc_flagged":
                out.append((d.get("net"), d.get("sta"), item.get("date")))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queue-dir", default="/mnt/seiscomp_staging/eqserver_sweep")
    ap.add_argument("--db-dirs", nargs="*", default=DEFAULT_DB_DIRS)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--n-uniform", type=int, default=4000)
    ap.add_argument("--per-unit-cap", type=int, default=8,
                    help="cap per (unit) within targeted strata")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    queue_dir = Path(args.queue_dir)
    db_dirs = [Path(d).expanduser() for d in args.db_dirs]

    units = promoted_units(queue_dir)
    conv_ts = first_convert_ts(queue_dir)
    station_days: dict[tuple, dict] = {}  # (net, sta) -> {year: [date, ...]}
    for net, sta in sorted({(n, s) for n, s, _ in units}):
        try:
            station_days[(net, sta)] = days_by_year(db_dirs, net, sta)
        except Exception as exc:
            print(f"WARN: no day index for {net}.{sta}: {exc}")
            station_days[(net, sta)] = {}
    day_index = {}  # unit -> [date, ...]
    for net, sta, year in units:
        day_index[(net, sta, year)] = station_days[(net, sta)].get(year, [])

    all_pairs = [(u, d) for u, days in day_index.items() for d in days]
    print(f"universe: {len(units)} units, {len(all_pairs)} station-days")

    strata: dict[str, list] = {}

    pool = all_pairs[:]
    rng.shuffle(pool)
    strata["uniform"] = pool[:args.n_uniform]

    strata["boundary"] = [
        (u, d) for u, days in day_index.items() for d in days
        if (d.month, d.day) in ((1, 1), (12, 31))]

    c2 = []
    for u, days in day_index.items():
        if u[1] in CLASS2_STATIONS and u[2] in CLASS2_YEARS and days:
            c2.extend((u, d) for d in rng.sample(
                days, min(args.per_unit_cap, len(days))))
    strata["class2"] = c2

    pre_c = []
    for u, days in day_index.items():
        if conv_ts.get(u, "9999") < PRE_C_CUTOFF and days:
            pre_c.extend((u, d) for d in rng.sample(
                days, min(args.per_unit_cap, len(days))))
    strata["pre_c"] = pre_c

    qc = []
    for net, sta, ds in qc_flagged_days(queue_dir):
        try:
            d = date_cls.fromisoformat(ds)
        except (ValueError, TypeError):
            continue
        u = (net, sta, d.year)
        if u in day_index:
            qc.append((u, d))
    strata["qc_flagged"] = qc

    for name, items in strata.items():
        rng.shuffle(items)
        print(f"stratum {name}: {len(items)}")

    # Round-robin interleave, dedup on (net, sta, date); first stratum wins.
    seen = set()
    queue = []
    order = ["boundary", "class2", "pre_c", "qc_flagged", "uniform"]
    idx = {k: 0 for k in order}
    while any(idx[k] < len(strata[k]) for k in order):
        for k in order:
            if idx[k] >= len(strata[k]):
                continue
            (net, sta, _year), d = strata[k][idx[k]]
            idx[k] += 1
            key = (net, sta, d)
            if key in seen:
                continue
            seen.add(key)
            queue.append({"net": net, "sta": sta, "date": d.isoformat(),
                          "stratum": k})
    with open(args.out, "w") as f:
        for item in queue:
            f.write(json.dumps(item) + "\n")
    print(f"wrote {len(queue)} work items -> {args.out}")


if __name__ == "__main__":
    main()

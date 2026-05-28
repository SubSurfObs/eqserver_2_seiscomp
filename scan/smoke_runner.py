#!/usr/bin/env python3
"""Cross-cohort smoke-test runner.

Loads scan/smoke_matrix.yaml, executes each entry against a Level-1 manifest +
registry, asserts the expected classifications hold. Returns non-zero exit code
on first failure (so CI/pre-commit can catch regressions).

Phase 2-only assertions are wired now: classification, recorder resolution,
plan_status. Phase 3 assertions (wnro_required, sds_output) are stubbed —
they fire when --check-sds is passed AND a Phase 3 SDS path is provided.

Run:
  python3 scan/smoke_runner.py <manifest.db> --registry metadata/station_registry.yaml
  python3 scan/smoke_runner.py <manifest.db> --registry ... --filter forg,brig,minimus
  python3 scan/smoke_runner.py <manifest.db> --registry ... --check-sds /mnt/seiscomp_staging/seiscomp_archive
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_manifest import classify, PER_DAY_SQL, MINIMUS_STATIONS_DEFAULT  # noqa: E402
from plan_generator import (  # noqa: E402
    load_registry, manifest_recorder, resolve_recorder_for_day, DEFER_CONVERSION_RECORDERS,
)

MATRIX_DEFAULT = os.path.join(HERE, "smoke_matrix.yaml")


def query_station_day(conn, station, date_str, minimus_set):
    """Query manifest for a (station, date) station-day row matching PER_DAY_SQL.
    Returns the row dict + computed classification + resolved recorder, or None
    if the manifest has no rows for that station-day.
    """
    year, month, day = (int(x) for x in date_str.split("-"))
    sql = PER_DAY_SQL.replace(
        "FROM files\nWHERE dir_year > 0",
        "FROM files\nWHERE dir_year > 0 AND station = ? AND dir_year = ? "
        "AND dir_month = ? AND dir_day = ?",
    )
    rows = list(conn.execute(sql, (station, year, month, day)))
    if not rows:
        return None
    r = rows[0]
    sc = "minimus" if r["station"] in minimus_set else None
    cls = classify(r, station_class=sc)
    rec_m = manifest_recorder(r)
    return {
        "row": r,
        "classification": cls,
        "recorder_inferred": rec_m,
    }


def check_entry(entry, conn, registry, minimus_set):
    """Runs all phase-2 assertions for one entry. Returns (ok, fails)."""
    sta = entry["station"]
    date = entry["date"]
    fails = []

    reg_entry = registry.get(sta)
    result = query_station_day(conn, sta, str(date), minimus_set)
    if result is None:
        return False, [f"manifest has no row for {sta} {date}"]

    cls = result["classification"]
    rec_m = result["recorder_inferred"]
    rec_resolved = resolve_recorder_for_day(rec_m, reg_entry)

    expected_class = entry.get("phase2_class")
    if expected_class and expected_class != "__mixed__" and cls != expected_class:
        fails.append(f"phase2_class: expected {expected_class}, got {cls}")

    expected_rec = entry.get("phase2_recorder")
    if expected_rec and expected_rec != "__any__" and rec_resolved != expected_rec:
        fails.append(f"phase2_recorder: expected {expected_rec}, got {rec_resolved}")

    expected_status = entry.get("plan_status")
    if expected_status:
        # quick-compute the per-station defer override (same shape as plan_generator)
        reg_rt = (reg_entry.get("recorder_types") if reg_entry else None) or []
        defer = any(rt in DEFER_CONVERSION_RECORDERS for rt in reg_rt)
        if defer and expected_status != "defer_conversion":
            fails.append(f"plan_status: expected {expected_status} but recorder is defer-listed")
        # If expected=defer_conversion, registry MUST flag it (registry annotation hygiene)
        if expected_status == "defer_conversion" and not defer:
            fails.append("plan_status=defer_conversion expected but recorder_types not in DEFER list")

    return (not fails), fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="Level-1 manifest SQLite path")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--matrix", default=MATRIX_DEFAULT)
    ap.add_argument("--filter", default="",
                    help="comma-separated entry id substrings; runs all matching")
    ap.add_argument("--check-sds", default="",
                    help="(Phase 3) staging SDS root; enables sds_output assertions")
    args = ap.parse_args()

    import yaml
    matrix = yaml.safe_load(open(args.matrix))
    entries = matrix.get("entries", [])
    if args.filter:
        substrs = [s.strip().lower() for s in args.filter.split(",") if s.strip()]
        entries = [e for e in entries
                   if any(s in e["id"].lower() for s in substrs)]

    registry = load_registry(args.registry)
    minimus_set = set(MINIMUS_STATIONS_DEFAULT)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    # Ensure the composite index exists (legacy DBs from before level1.py was
    # updated to pre-build it). One-time cost on first-touch of a legacy DB;
    # IF NOT EXISTS makes the call free on already-indexed DBs.
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station_dir ON files(station, dir_year, dir_month, dir_day)")
    conn.commit()

    print(f"[smoke] {len(entries)} entries from {os.path.basename(args.matrix)} "
          f"against {os.path.basename(args.db)}")
    results = Counter()
    fail_records = []

    for e in entries:
        ok, fails = check_entry(e, conn, registry, minimus_set)
        results["pass" if ok else "fail"] += 1
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {e['id']:<35} {e['station']:>6} {e['date']}")
        for f in fails:
            print(f"         -> {f}")
            fail_records.append((e["id"], f))

    print(f"\n[smoke] {results['pass']} pass / {results['fail']} fail (of {len(entries)})")
    if args.check_sds:
        print("[smoke] note: --check-sds is a Phase 3 hook; SDS assertions not yet wired.")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

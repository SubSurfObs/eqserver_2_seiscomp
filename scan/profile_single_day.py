#!/usr/bin/env python3
"""Single-day timing breakdown for phase3.

Quantifies where wallclock goes in a single day-job: DB query, NFS read,
decode/parse, merge/sort, atomic SDS write. Picks one or more sample days
across cohorts and reports mean per-phase timings.

Intended use: Exp 9 in PERFORMANCE.md. Now that `python-isal` is already
deployed (Exp 8 deployed-by-default), this experiment tells us what the
NEXT bottleneck is.

Run (after a Round 1 finishes — needs an idle VM for clean numbers):
  python3 scan/profile_single_day.py \
      --station-dbs /home/.../station_dbs \
      --plans /tmp/plans_vw \
      --registry metadata/station_registry.yaml \
      --out /tmp/profile_2026-05-31

Output: prints a per-cohort phase breakdown table; writes raw timings to
out/results.jsonl.
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sqlite3
import sys
import time
import yaml
from collections import defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DEFAULT_DTSD = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/scripts"


def time_block(label, fn):
    """Run fn() and return (label, elapsed_s, result)."""
    t0 = time.perf_counter()
    r = fn()
    return label, time.perf_counter() - t0, r


def profile_echopro_day(suds_convert, conn, sta, net, loc, d, staging_root,
                        disk_size_floor):
    """One echopro day-job, phase-timed.

    Phases:
      - db_query: cross-source selection
      - nfs_decode: convert_suds_files (NFS read + gzip inflate + SUDS parse
        + obspy Stream build; can't cleanly separate within sudspy without
        intrusive instrumentation, so report as one block)
      - merge_sort: phase3 already calls into convert_suds_files which does
        the merge internally; the externally-visible merge cost here is just
        the location overwrite (negligible)
      - sds_write: write_sds (encode + atomic write)
    """
    from cross_source import select_files_for_day, DEFAULT_DISK_SIZE_FLOOR_RATIO  # noqa: E402
    sql = (
        "SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
        "WHERE station=? AND dir_year=? AND dir_month=? AND dir_day=? "
        "  AND recorder_type='echopro' AND exclude_reason IS NULL"
    )
    rows_t0 = time.perf_counter()
    rows = conn.execute(sql, (sta, d.year, d.month, d.day)).fetchall()
    db_query_s = time.perf_counter() - rows_t0

    files_t0 = time.perf_counter()
    files = select_files_for_day(rows, disk_size_floor_ratio=disk_size_floor)
    select_s = time.perf_counter() - files_t0
    if not files:
        return None

    nfs_t0 = time.perf_counter()
    stream, qc = suds_convert.convert_suds_files(files, network=net, station=sta)
    nfs_decode_s = time.perf_counter() - nfs_t0

    merge_t0 = time.perf_counter()
    for tr in stream:
        tr.stats.location = loc
    merge_sort_s = time.perf_counter() - merge_t0

    write_t0 = time.perf_counter()
    written = suds_convert.write_sds(stream, staging_root) if stream else []
    write_s = time.perf_counter() - write_t0

    return {
        "cohort": "echopro", "station": sta, "date": d.isoformat(),
        "n_files": len(files),
        "n_traces": len(stream),
        "bytes_written": sum(sz for _, sz in written),
        "phases": {
            "db_query_s": round(db_query_s, 4),
            "select_s": round(select_s, 4),
            "nfs_decode_s": round(nfs_decode_s, 4),
            "merge_sort_s": round(merge_sort_s, 4),
            "sds_write_s": round(write_s, 4),
            "total_s": round(db_query_s + select_s + nfs_decode_s + merge_sort_s + write_s, 4),
        },
    }


def profile_gecko_day(suds_convert, conn, sta, net, loc, d, staging_root,
                      disk_size_floor, recorder_filter="gecko"):
    """One gecko (or minimus) day-job, phase-timed. Gecko/minimus split
    nfs_read from decode cleanly because _concat_zip_members is purely
    NFS+memcpy and obspy.read is purely decode.
    """
    from cross_source import select_files_for_day, DEFAULT_DISK_SIZE_FLOOR_RATIO  # noqa: E402
    if recorder_filter == "minimus":
        sql = ("SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
               "WHERE station=? AND dir_year=? AND dir_month=? AND dir_day=? "
               "  AND recorder_type='mseed' AND exclude_reason='single_channel'")
    else:
        sql = ("SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
               "WHERE station=? AND dir_year=? AND dir_month=? AND dir_day=? "
               "  AND recorder_type=? AND exclude_reason IS NULL")
    rows_t0 = time.perf_counter()
    params = ((sta, d.year, d.month, d.day) if recorder_filter == "minimus"
              else (sta, d.year, d.month, d.day, recorder_filter))
    rows = conn.execute(sql, params).fetchall()
    db_query_s = time.perf_counter() - rows_t0

    sel_t0 = time.perf_counter()
    files = select_files_for_day(rows, disk_size_floor_ratio=disk_size_floor)
    select_s = time.perf_counter() - sel_t0
    if not files:
        return None

    # NFS read = read whole .ms.zip into BytesIO + extract member bytes
    import zipfile
    nfs_t0 = time.perf_counter()
    combined = io.BytesIO()
    read_errors = []
    for path in files:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    if name.endswith((".ms", ".mseed")):
                        combined.write(z.read(name))
                        break
        except Exception as e:
            read_errors.append((path, str(e)))
    nfs_read_s = time.perf_counter() - nfs_t0
    combined.seek(0)

    # Decode = ObsPy read of the concatenated mseed
    from obspy import read as obspy_read
    decode_t0 = time.perf_counter()
    stream = obspy_read(combined, format="MSEED")
    decode_s = time.perf_counter() - decode_t0

    # Merge + sort + location overwrite
    merge_t0 = time.perf_counter()
    for tr in stream:
        tr.stats.network = net
        tr.stats.location = loc
    stream.merge(method=1, fill_value=None)
    stream = stream.split()
    stream.sort(["starttime"])
    merge_sort_s = time.perf_counter() - merge_t0

    # SDS write
    write_t0 = time.perf_counter()
    written = suds_convert.write_sds(stream, staging_root) if stream else []
    write_s = time.perf_counter() - write_t0

    return {
        "cohort": recorder_filter, "station": sta, "date": d.isoformat(),
        "n_files": len(files),
        "n_traces": len(stream),
        "bytes_written": sum(sz for _, sz in written),
        "phases": {
            "db_query_s": round(db_query_s, 4),
            "select_s": round(select_s, 4),
            "nfs_read_s": round(nfs_read_s, 4),
            "decode_s": round(decode_s, 4),
            "merge_sort_s": round(merge_sort_s, 4),
            "sds_write_s": round(write_s, 4),
            "total_s": round(db_query_s + select_s + nfs_read_s + decode_s
                             + merge_sort_s + write_s, 4),
        },
    }


# Hand-picked sample days that we know have data — one per cohort, from
# stations seen in Round 1. These should be CLEAN days (no flagged_days
# overlap) so the profile reflects normal-path costs.
SAMPLES = [
    # cohort,      station, NET, date,       recorder_filter
    ("echopro",    "OUTU",  "VW", "2023-06-15", "echopro"),
    ("echopro",    "BRIG",  "VW", "2023-04-12", "echopro"),
    ("echopro",    "MARD",  "VW", "2024-03-08", "echopro"),
    ("gecko",      "WDSD",  "VW", "2023-08-14", "gecko"),
    ("gecko",      "STBK",  "VW", "2023-06-20", "gecko"),
    ("gecko",      "WLSH",  "VW", "2023-09-25", "gecko"),
    ("rt130",      "SGWU",  "VW", "2023-03-15", "gecko"),    # RT130 via gecko branch
    ("minimus",    "DDWB",  "VW", "2023-05-10", "minimus"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station-dbs", required=True)
    ap.add_argument("--plans", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out", default="/tmp/profile_2026-05-31")
    ap.add_argument("--disk-to-sds", default=DEFAULT_DTSD)
    ap.add_argument("--disk-size-floor-ratio", type=float, default=0.8)
    ap.add_argument("--samples", default="",
                    help="comma-separated cohort labels to run "
                         "(default: all sampled days)")
    args = ap.parse_args()

    sys.path.insert(0, args.disk_to_sds)
    import suds_convert

    wanted_cohorts = {s.strip() for s in args.samples.split(",") if s.strip()}
    samples = [s for s in SAMPLES
               if not wanted_cohorts or s[0] in wanted_cohorts]

    os.makedirs(args.out, exist_ok=True)
    out_jsonl = os.path.join(args.out, "results.jsonl")
    open(out_jsonl, "w").close()

    print(f"[profile] running {len(samples)} sample days, output to {args.out}")
    print(f"{'cohort':10} {'sta':6} {'date':12} {'n_files':>7} {'total_s':>8} "
          f"{'phases':40}")

    per_cohort = defaultdict(list)
    for cohort, sta, net, date_iso, rec_filter in samples:
        # Find plan + DB for this station
        plan_path = os.path.join(args.plans, f"VW.{sta}.plan.yaml")
        db_path = os.path.join(args.station_dbs, f"VW.{sta}.db")
        if not os.path.exists(plan_path) or not os.path.exists(db_path):
            print(f"  {cohort:10} {sta:6} SKIP — plan/DB missing")
            continue
        plan = yaml.safe_load(open(plan_path))
        loc = plan.get("location", "00")
        d = date.fromisoformat(date_iso)
        conn = sqlite3.connect(db_path)
        # Each profile writes to a per-cohort scratch dir so concurrent
        # write isolation is not a concern for this measurement.
        staging = os.path.join(args.out, "sds", cohort, sta)
        os.makedirs(staging, exist_ok=True)
        try:
            if cohort == "echopro":
                result = profile_echopro_day(suds_convert, conn, sta, net, loc,
                                             d, staging, args.disk_size_floor_ratio)
            else:
                result = profile_gecko_day(suds_convert, conn, sta, net, loc,
                                           d, staging, args.disk_size_floor_ratio,
                                           recorder_filter=rec_filter)
        except Exception as e:
            import traceback
            print(f"  {cohort:10} {sta:6} ERROR {type(e).__name__}: {e}")
            traceback.print_exc()
            conn.close()
            continue
        conn.close()
        if result is None:
            print(f"  {cohort:10} {sta:6} no files on {date_iso}")
            continue
        per_cohort[cohort].append(result)
        phases = result["phases"]
        phase_str = " ".join(f"{k.replace('_s','')}={v:.2f}" for k, v in phases.items()
                             if k != "total_s")
        print(f"  {cohort:10} {sta:6} {date_iso:12} {result['n_files']:>7} "
              f"{phases['total_s']:>8.2f} {phase_str}")
        with open(out_jsonl, "a") as f:
            f.write(json.dumps(result) + "\n")

    # Per-cohort mean breakdown
    print()
    print("=== Per-cohort mean phase breakdown ===")
    for cohort, rs in per_cohort.items():
        if not rs:
            continue
        phase_keys = list(rs[0]["phases"].keys())
        means = {k: sum(r["phases"][k] for r in rs) / len(rs) for k in phase_keys}
        mean_total = means["total_s"]
        print(f"\n[{cohort}] n={len(rs)} mean total={mean_total:.2f}s")
        for k in phase_keys:
            if k == "total_s":
                continue
            pct = 100 * means[k] / mean_total if mean_total > 0 else 0
            print(f"  {k:15} {means[k]:>6.3f}s  ({pct:>5.1f}%)")


if __name__ == "__main__":
    sys.exit(main())

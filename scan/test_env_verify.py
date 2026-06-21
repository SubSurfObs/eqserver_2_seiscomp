#!/usr/bin/env python3
"""test_env_verify.py — compare test env SDS output against source bytes.

For a (sta, year-mm-dd) registered in the test env catalogue, this:

  1. Opens the test env's SDS day-file for each channel (CHZ/CHN/CHE).
  2. Identifies "gaps" in the trace — places where the SDS day-file does
     NOT have a continuous trace covering the expected span [00:00:00,
     24:00:00) at the day's sample rate.
  3. For each gap, queries the time_index DB to find any source files
     that DID contain that span+channel.
  4. Classifies every gap:
       - pipeline_loss : source had it, SDS dropped it (a real bug)
       - source_gap    : no source file ever contained it (honest gap)
  5. Reports per-channel: total samples in SDS, expected for full day,
     gap count by class, sample loss attributable to each class.

USAGE:
  python3 scan/test_env_verify.py STBK 2022-10-23
  python3 scan/test_env_verify.py HOLS 2018-06-15 --json   # machine-readable
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Re-use test_env_build's paths
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_env_build import (  # noqa: E402
    TIME_INDEX, STAGING_SDS, load_catalogue,
)

DEFAULT_SAMPLE_RATE = 250.0


def parse_iso(s: str):
    """Parse the ISO format we wrote to time_index (with 'Z' suffix)."""
    from obspy import UTCDateTime
    return UTCDateTime(s)


# --------------------------------------------------------------------------
# Read SDS day-file, identify gaps
# --------------------------------------------------------------------------

def find_gaps(sds_path: Path, day_start, day_end, sample_rate: float,
              tolerance_samples: int = 2) -> tuple[list, int]:
    """Return (list of (gap_start_utc, gap_end_utc), total_samples_in_file).

    A gap is any interval in [day_start, day_end) that the SDS file does
    NOT cover. A gap of less than tolerance_samples (= ~8 ms at 250 Hz)
    is ignored — those are inevitable sub-sample-period offsets at
    record boundaries.
    """
    import warnings
    warnings.filterwarnings("ignore")
    from obspy import read, UTCDateTime
    st = read(str(sds_path), format="MSEED")
    st.sort(["starttime"])
    total_npts = sum(tr.stats.npts for tr in st)

    gaps = []
    delta = 1.0 / sample_rate
    tolerance_seconds = tolerance_samples * delta

    # Walk traces, identify uncovered regions in [day_start, day_end)
    cursor = day_start
    for tr in st:
        # Clip trace to day window
        s = max(tr.stats.starttime, day_start)
        e = min(tr.stats.endtime, day_end)
        if e <= cursor:
            # Already past, or entirely before this point
            continue
        if s > cursor + tolerance_seconds:
            # Real gap before this trace
            gaps.append((cursor, s))
        cursor = max(cursor, e + delta)
        if cursor >= day_end:
            break
    if cursor < day_end - tolerance_seconds:
        gaps.append((cursor, day_end))

    return gaps, total_npts


# --------------------------------------------------------------------------
# Time-index query: which source files SHOULD have covered this gap
# --------------------------------------------------------------------------

def query_source_for_span(time_index_db: Path, sta: str, chan: str,
                           span_start, span_end) -> list[dict]:
    """Return source files whose trace overlaps [span_start, span_end)
    for this channel."""
    conn = sqlite3.connect(time_index_db)
    rows = conn.execute(
        "SELECT source_kind, source_path, trace_start_iso, trace_end_iso, "
        "npts, sample_rate FROM time_index "
        "WHERE sta = ? AND chan = ? "
        "  AND trace_end_iso > ? AND trace_start_iso < ?",
        (sta, chan, str(span_start).rstrip("Z") + "Z",
         str(span_end).rstrip("Z") + "Z")
    ).fetchall()
    conn.close()
    return [
        {"source_kind": r[0], "source_path": r[1],
         "trace_start": r[2], "trace_end": r[3],
         "npts": r[4], "sample_rate": r[5]}
        for r in rows
    ]


# --------------------------------------------------------------------------
# Verify one channel
# --------------------------------------------------------------------------

def verify_channel(sta: str, chan: str, doy: int, year: int,
                   day_start, day_end, sample_rate: float,
                   time_index_db: Path) -> dict:
    """Verify one channel's SDS output for the day."""
    sds_path = (STAGING_SDS / f"{year:04d}" / "VW" / sta / f"{chan}.D" /
                f"VW.{sta}.00.{chan}.D.{year:04d}.{doy:03d}")
    if not sds_path.exists():
        return {
            "chan": chan,
            "sds_path": str(sds_path),
            "sds_exists": False,
            "error": "SDS day-file not found",
        }

    gaps, total_npts = find_gaps(sds_path, day_start, day_end, sample_rate)
    expected_npts = int(round(86400 * sample_rate))

    # Classify each gap
    gap_records = []
    pipeline_loss_samples = 0
    source_gap_samples = 0
    for gap_start, gap_end in gaps:
        contributing = query_source_for_span(time_index_db, sta, chan,
                                             gap_start, gap_end)
        gap_seconds = float(gap_end - gap_start)
        gap_samples = int(round(gap_seconds * sample_rate))
        # Bucket: any source overlap → pipeline_loss; else source_gap
        if contributing:
            klass = "pipeline_loss"
            pipeline_loss_samples += gap_samples
        else:
            klass = "source_gap"
            source_gap_samples += gap_samples
        gap_records.append({
            "start": str(gap_start),
            "end": str(gap_end),
            "duration_s": round(gap_seconds, 3),
            "samples": gap_samples,
            "class": klass,
            "n_source_files": len(contributing),
            "sample_source_kinds": [c["source_kind"] for c in contributing[:3]],
        })

    return {
        "chan": chan,
        "sds_path": str(sds_path),
        "sds_exists": True,
        "expected_npts": expected_npts,
        "actual_npts": total_npts,
        "pct_coverage": round(100 * total_npts / expected_npts, 3),
        "n_gaps": len(gap_records),
        "pipeline_loss_samples": pipeline_loss_samples,
        "source_gap_samples": source_gap_samples,
        "gaps": gap_records,
    }


# --------------------------------------------------------------------------
# Main: verify one day, all channels
# --------------------------------------------------------------------------

def verify_day(sta: str, year: int, month: int, day: int) -> dict:
    from obspy import UTCDateTime
    day_start = UTCDateTime(year, month, day, 0, 0, 0)
    day_end = day_start + 86400
    doy = day_start.julday

    time_index_db = TIME_INDEX / f"{sta}.time_index.db"
    if not time_index_db.exists():
        return {"error": f"time_index missing: {time_index_db}. "
                f"Run test_env_build add or rebuild_time_index first."}

    out = {
        "sta": sta,
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "doy": doy,
        "channels": {},
        "summary": {},
    }

    total_pipeline_loss = 0
    total_source_gap = 0
    total_actual = 0
    total_expected = 0
    for chan in ("CHZ", "CHN", "CHE"):
        r = verify_channel(sta, chan, doy, year, day_start, day_end,
                           DEFAULT_SAMPLE_RATE, time_index_db)
        out["channels"][chan] = r
        if r.get("sds_exists"):
            total_pipeline_loss += r["pipeline_loss_samples"]
            total_source_gap += r["source_gap_samples"]
            total_actual += r["actual_npts"]
            total_expected += r["expected_npts"]

    out["summary"] = {
        "total_actual_samples": total_actual,
        "total_expected_samples": total_expected,
        "total_pct_coverage": round(100 * total_actual / total_expected, 3)
        if total_expected else 0.0,
        "pipeline_loss_samples": total_pipeline_loss,
        "source_gap_samples": total_source_gap,
        "verdict": _verdict(out["channels"], total_pipeline_loss, total_actual,
                            total_expected),
    }
    return out


def _verdict(channels: dict, pipeline_loss: int, actual: int, expected: int) -> str:
    if expected == 0:
        return "ERROR_no_expected_samples"
    missing_channels = [c for c, r in channels.items()
                        if not r.get("sds_exists")]
    if missing_channels:
        return f"FAIL_missing_channels:{','.join(missing_channels)}"
    if pipeline_loss > 1000:  # > ~4 sec worth at 250 Hz
        return f"FAIL_pipeline_loss:{pipeline_loss}_samples"
    pct = 100 * actual / expected
    if pct >= 99.5:
        return "PASS_clean"
    if pct >= 99.0:
        return "PASS_minor_source_gaps"
    return f"FAIL_low_coverage:{pct:.2f}%"


# --------------------------------------------------------------------------
# Pretty printing
# --------------------------------------------------------------------------

def print_human(out: dict) -> None:
    if "error" in out:
        print(f"ERROR: {out['error']}")
        return
    print(f"[verify] {out['sta']} {out['date']} (doy {out['doy']})")
    for chan, r in out["channels"].items():
        if not r.get("sds_exists"):
            print(f"  {chan}: MISSING — {r.get('error')}")
            continue
        print(f"  {chan}: coverage {r['pct_coverage']:.2f}% "
              f"({r['actual_npts']}/{r['expected_npts']})  "
              f"gaps={r['n_gaps']} (pipeline_loss={r['pipeline_loss_samples']} "
              f"source_gap={r['source_gap_samples']})")
        # Show up to 3 gap details
        for g in r["gaps"][:3]:
            print(f"      {g['class']:14s} {g['start']} → {g['end']}  "
                  f"({g['duration_s']:.3f}s, {g['samples']} samples, "
                  f"{g['n_source_files']} source files)")
        if len(r["gaps"]) > 3:
            print(f"      ... ({len(r['gaps']) - 3} more)")
    s = out["summary"]
    print(f"  SUMMARY: {s['total_pct_coverage']:.2f}% coverage; "
          f"pipeline_loss={s['pipeline_loss_samples']}, "
          f"source_gap={s['source_gap_samples']}")
    print(f"  VERDICT: {s['verdict']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON to stdout (otherwise human-readable)")
    args = ap.parse_args()

    sta = args.sta
    year, month, day = (int(p) for p in args.date.split("-"))

    # Make sure the day is registered
    entries = load_catalogue()
    if not any(e["sta"] == sta and e["year"] == year and e["month"] == month
               and e["day"] == day for e in entries):
        print(f"ERROR: {sta} {args.date} not in test env catalogue. "
              f"Run test_env_build.py add first.", file=sys.stderr)
        return 2

    out = verify_day(sta, year, month, day)

    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print_human(out)
    return 0 if out.get("summary", {}).get("verdict", "").startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())

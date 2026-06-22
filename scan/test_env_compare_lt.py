#!/usr/bin/env python3
"""test_env_compare_lt.py — bulk staging-vs-LT comparison for catalogue.

For every day in the test env catalogue with non-empty staging output,
read its SDS day-files and compare against the long-term archive at
/mnt/seiscomp_archive. Classifies each channel as:

  WRITE     - staging exists, LT empty -> apply.py would copy across
  MATCH     - staging and LT have the same sample count (within tolerance)
  OVERRIDE  - staging has materially more samples than LT
  CLIP      - staging has fewer samples than LT (would lose data)
  ERROR     - read error on either side

Aggregates per-bucket verdicts so the test-suite outcome is a single
go/no-go table.

USAGE:
  python3 scan/test_env_compare_lt.py
  python3 scan/test_env_compare_lt.py --stations BEST,STBK --json out.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

STAGING_BASE = Path("/mnt/seiscomp_staging/test_env_classb/staging_sds")
LT_BASE = Path("/mnt/seiscomp_archive")
CATALOGUE_PATH = Path("/home/unimelb.edu.au/dsand/test_env_classb/catalogue.yaml")

# Verdict thresholds (samples). At 250 Hz one minute is 15,000 samples;
# a "match" tolerance of one minute is generous and avoids edge-trim noise.
MATCH_TOLERANCE_SAMPLES = 15000


def file_md5(path: str, chunk: int = 1 << 20):
    """Return md5 hex of a file's bytes, or None on error."""
    import hashlib
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def trace_summary(path: str):
    """Read SDS day-file and report:
       - n_traces: number of mseed records (FRAGMENTATION SIGNAL — high
         counts indicate Class B merge bug at convert time)
       - n_samples: total sample count summed across traces
       - rate_hz: sampling rate of first trace
       - bytes_on_disk: file size (different sizes with same n_samples
         indicate different encoding or fragmentation)
       - md5: hex of file bytes for TRUE byte-equality comparison
    """
    import os
    from obspy import read
    try:
        bytes_on_disk = os.path.getsize(path)
    except OSError:
        bytes_on_disk = None
    try:
        st = read(path, format="MSEED")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}",
                "bytes_on_disk": bytes_on_disk}
    if not st:
        return {"n_traces": 0, "n_samples": 0, "rate_hz": 0.0,
                "bytes_on_disk": bytes_on_disk,
                "md5": file_md5(path)}
    return {
        "n_traces": len(st),
        "n_samples": sum(tr.stats.npts for tr in st),
        "rate_hz": float(st[0].stats.sampling_rate),
        "bytes_on_disk": bytes_on_disk,
        "md5": file_md5(path),
    }


# Threshold for "n_traces materially different": if the lt has >=10x more
# traces than staging at the SAME sample count, that's a Class B fix-visible
# difference (rescan consolidated traces the broken scan-1 merge had left
# fragmented). 10x is generous — actual Class B days had 1000s vs 1-3.
N_TRACES_FRAGMENTATION_RATIO = 10


def compare_channel(sta: str, year: int, doy: int, chan: str):
    chan_dir = f"{chan}.D"
    fname = f"VW.{sta}.00.{chan}.D.{year:04d}.{doy:03d}"
    st_path = STAGING_BASE / f"{year:04d}" / "VW" / sta / chan_dir / fname
    lt_path = LT_BASE / f"{year:04d}" / "VW" / sta / chan_dir / fname
    if not st_path.exists():
        return {"chan": chan, "verdict": "NO_STAGING",
                "staging": None, "lt_exists": lt_path.exists()}
    s = trace_summary(str(st_path))
    if "error" in s:
        return {"chan": chan, "verdict": "ERROR_staging", "error": s["error"]}
    if not lt_path.exists():
        return {"chan": chan, "verdict": "WRITE",
                "staging_samples": s["n_samples"],
                "staging_n_traces": s.get("n_traces"),
                "staging_bytes": s.get("bytes_on_disk"),
                "lt_samples": 0,
                "rate_hz": s["rate_hz"]}
    lt = trace_summary(str(lt_path))
    if "error" in lt:
        return {"chan": chan, "verdict": "ERROR_lt", "error": lt["error"]}

    diff = s["n_samples"] - lt["n_samples"]

    # Stage 1: classify by sample-count diff (the existing logic)
    if diff > MATCH_TOLERANCE_SAMPLES:
        sample_verdict = "OVERRIDE"
    elif diff < -MATCH_TOLERANCE_SAMPLES:
        sample_verdict = "CLIP"
    else:
        sample_verdict = "SAMPLE_MATCH"   # placeholder — refined below

    # Stage 2: when samples are equivalent, refine by byte-level equality
    # and fragmentation signal.
    if sample_verdict == "SAMPLE_MATCH":
        if s.get("md5") and s.get("md5") == lt.get("md5"):
            verdict = "BYTE_EQUAL"
        else:
            # Bytes differ even though samples match. Two sub-cases:
            #  - LT was Class-B-fragmented and rescan consolidated traces
            #  - Different encoding / record framing for unknown reason
            lt_traces = lt.get("n_traces", 0) or 0
            st_traces = s.get("n_traces", 0) or 0
            if st_traces > 0 and lt_traces >= st_traces * N_TRACES_FRAGMENTATION_RATIO:
                verdict = "DEFRAGMENTED"   # Class B fix visible
            elif lt_traces > 0 and st_traces >= lt_traces * N_TRACES_FRAGMENTATION_RATIO:
                verdict = "REFRAGMENTED"   # regression (rescan worse than LT) — should never happen
            else:
                verdict = "BYTES_DIFFER"   # ambiguous: same samples, diff bytes, similar n_traces
    else:
        verdict = sample_verdict

    return {"chan": chan, "verdict": verdict,
            "staging_samples": s["n_samples"], "lt_samples": lt["n_samples"],
            "diff_samples": diff, "rate_hz": s["rate_hz"],
            "staging_n_traces": s.get("n_traces"),
            "lt_n_traces": lt.get("n_traces"),
            "staging_bytes": s.get("bytes_on_disk"),
            "lt_bytes": lt.get("bytes_on_disk"),
            "byte_equal": s.get("md5") == lt.get("md5") if s.get("md5") and lt.get("md5") else None}


def compare_day(sta: str, year: int, month: int, day: int):
    doy = date(year, month, day).timetuple().tm_yday
    staging_dir = STAGING_BASE / f"{year:04d}" / "VW" / sta
    if not staging_dir.exists():
        return {"sta": sta, "year": year, "doy": doy,
                "verdict": "NO_STAGING", "channels": []}
    chans = sorted({d.name[:-2] for d in staging_dir.iterdir()
                    if d.is_dir() and d.name.endswith(".D")})
    channel_results = [compare_channel(sta, year, doy, c) for c in chans]

    # Filter out empty-channel-dir artefacts: leftover <CHAN>.D directories
    # from a previous catalogue's sample-rate epoch. A channel result is
    # "real" only if EITHER side has data for THIS specific DOY.
    real_channels = []
    for r in channel_results:
        staging_present = (
            r.get("staging_samples") is not None and r.get("staging_samples", 0) > 0
        )
        lt_present = (
            r.get("lt_samples") is not None and r.get("lt_samples", 0) > 0
        ) or r.get("verdict") in ("WRITE",)
        if staging_present or lt_present:
            real_channels.append(r)

    # Day verdict: worst of REAL channels by priority. If no real channels
    # for either side, the day is genuinely empty -> NO_STAGING.
    severity = {
        "ERROR_staging":   10,
        "ERROR_lt":         9,
        "NO_STAGING":       8,
        "REFRAGMENTED":     7,   # regression: rescan more fragmented than LT
        "CLIP":             6,   # would lose LT data
        "OVERRIDE":         5,   # needs held-queue review
        "BYTES_DIFFER":     4,   # same samples, diff bytes, similar n_traces
        "DEFRAGMENTED":     3,   # CLASS B FIX VISIBLE — rescan merged what scan-1 fragmented
        "WRITE":            2,   # fresh write (recovery)
        "BYTE_EQUAL":       1,   # true byte-equal output
        "MATCH":            0,
    }
    if real_channels:
        worst = max(real_channels, key=lambda r: severity.get(r["verdict"], 0))
        day_verdict = worst["verdict"]
    elif channel_results:
        day_verdict = "NO_STAGING"
    else:
        day_verdict = "NO_CHANNELS"
    return {"sta": sta, "year": year, "month": month, "day": day,
            "doy": doy, "verdict": day_verdict, "channels": channel_results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", default=None,
                   help="comma-separated station filter")
    ap.add_argument("--buckets", default=None,
                   help="comma-separated bucket-category filter")
    ap.add_argument("--max", type=int, default=None,
                   help="limit to first N catalogue entries (after filters)")
    ap.add_argument("--json", default=None,
                   help="write JSON output to this path")
    args = ap.parse_args()

    import yaml
    entries = yaml.safe_load(CATALOGUE_PATH.read_text()) or []
    if args.stations:
        wanted = set(args.stations.split(","))
        entries = [e for e in entries if e["sta"] in wanted]
    if args.buckets:
        wanted = set(args.buckets.split(","))
        entries = [e for e in entries if e.get("category") in wanted]
    if args.max:
        entries = entries[: args.max]

    print(f"[compare_lt] {len(entries)} catalogue entries to check", flush=True)
    results = []
    by_verdict = {}
    by_bucket = {}
    for i, e in enumerate(entries, 1):
        sta, y, m, d = e["sta"], e["year"], e["month"], e["day"]
        bucket = e.get("category", "")
        r = compare_day(sta, y, m, d)
        r["category"] = bucket
        results.append(r)
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
        by_bucket.setdefault(bucket, {})[r["verdict"]] = \
            by_bucket.setdefault(bucket, {}).get(r["verdict"], 0) + 1
        if i % 20 == 0 or i == len(entries):
            print(f"  [{i}/{len(entries)}] {sta} {y}-{m:02d}-{d:02d} -> {r['verdict']}",
                  flush=True)

    print(f"\n=== Verdict totals ===")
    for v, n in sorted(by_verdict.items(), key=lambda x: -x[1]):
        pct = 100 * n / len(entries) if entries else 0
        print(f"  {v:18s} {n:>4} ({pct:5.1f}%)")

    print(f"\n=== Verdict by bucket category ===")
    cols = ["BYTE_EQUAL", "DEFRAGMENTED", "BYTES_DIFFER", "WRITE",
            "OVERRIDE", "CLIP", "REFRAGMENTED", "NO_STAGING",
            "ERROR_staging", "ERROR_lt"]
    print(f"  {'bucket':32s} {'total':>5s} " +
          "  ".join(f"{v:>13s}" for v in cols))
    for bucket in sorted(by_bucket):
        d = by_bucket[bucket]
        total = sum(d.values())
        cells = [f"{d.get(v, 0):>13}" for v in cols]
        print(f"  {bucket:32s} {total:>5} " + "  ".join(cells))

    if args.json:
        Path(args.json).write_text(json.dumps({
            "n_entries": len(entries),
            "by_verdict": by_verdict,
            "by_bucket": by_bucket,
            "results": results,
        }, indent=2))
        print(f"\nWritten: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""compare_lt — staged SDS vs long-term SDS, per day-channel, by exact sample
time and value (Codex reviews 2026-11-18 F1/F2 and 2026-11-24).

For every staged day-file of one station-year, the long-term (LT) file at the
same SDS path is compared sample by sample on exact integer-nanosecond times:

  write      LT has no file at this path
  identical  same set of (time, value) samples
  superset   every LT sample exists in staging at the same time with the same
             value, and staging holds more            -> safe to replace
  conflict   some LT sample is absent from staging or has a different value
                                                      -> hold for review
  unreadable either file could not be decoded         -> hold for review

A staged sample is only "the same time" as an LT sample when it lies on the
same grid at the same nanosecond; nothing is re-timed or interpolated.

Every verdict carries the sha256 of the staged and LT files it was computed
from, so the decision can be re-checked against the bytes that get promoted.
Read-only: it never writes to staging or LT. Output: JSON lines.

Usage: compare_lt.py --staging-root S --lt-root L --net VW --sta LOCU \
           --year 2016 [--workers 8] --out results.jsonl
"""
import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

NS = 1_000_000_000


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _segments(path):
    """[(t0_ns, dt_ns, data int64)] per trace, as recorded (no merging)."""
    from obspy import read
    out = []
    for tr in read(path, format="MSEED"):
        rate = float(tr.stats.sampling_rate)
        if rate <= 0 or len(tr.data) == 0:
            continue
        dt = round(NS / rate)
        out.append((int(tr.stats.starttime.ns), dt, np.asarray(tr.data, dtype=np.int64)))
    return out


def _samples(segs):
    """Every sample as one sorted (time_ns, value) structured array; duplicate
    times inside one file are kept (they are a finding, not merged away)."""
    if not segs:
        return np.zeros(0, dtype=[("t", np.int64), ("v", np.int64)])
    ts, vs = [], []
    for t0, dt, d in segs:
        ts.append(t0 + dt * np.arange(len(d), dtype=np.int64))
        vs.append(d)
    a = np.empty(sum(len(x) for x in ts), dtype=[("t", np.int64), ("v", np.int64)])
    a["t"] = np.concatenate(ts)
    a["v"] = np.concatenate(vs)
    a.sort(order=("t", "v"))
    return a


def compare_pair(staged, lt):
    """Verdict dict for one staged file against its LT counterpart."""
    rec = {"staged": str(staged), "lt": str(lt), "staged_sha256": _sha256(staged)}
    if not os.path.exists(lt):
        rec["verdict"] = "write"
        return rec
    rec["lt_sha256"] = _sha256(lt)
    try:
        s = _samples(_segments(staged))
        lsa = _samples(_segments(lt))
    except Exception as e:
        rec.update(verdict="unreadable", error=f"{type(e).__name__}: {e}"[:300])
        return rec
    rec["n_staged"], rec["n_lt"] = int(len(s)), int(len(lsa))
    # LT samples whose exact (time, value) pair is present in staging
    found = np.isin(lsa.view(np.void(16)), s.view(np.void(16)))
    n_match = int(found.sum())
    if n_match < len(lsa):
        missing = lsa[~found]
        same_time = np.isin(missing["t"], s["t"])
        rec.update(verdict="conflict",
                   lt_samples_not_in_staging=int((~same_time).sum()),
                   lt_samples_value_differs=int(same_time.sum()),
                   first_conflict_ns=int(missing["t"][0]))
        return rec
    rec["verdict"] = "identical" if len(s) == len(lsa) else "superset"
    rec["staged_extra_samples"] = int(len(s) - len(lsa))
    return rec


def _pairs(staging_root, lt_root, net, sta, year):
    base = Path(staging_root) / str(year) / net / sta
    for f in sorted(base.glob("*.D/*")):
        if f.is_file() and not f.name.endswith(".partial"):
            yield f, Path(lt_root) / f.relative_to(staging_root)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging-root", required=True)
    ap.add_argument("--lt-root", required=True)
    ap.add_argument("--net", required=True)
    ap.add_argument("--sta", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pairs = list(_pairs(a.staging_root, a.lt_root, a.net, a.sta, a.year))
    counts = {}
    tmp = a.out + ".partial"
    with open(tmp, "w") as out, ProcessPoolExecutor(max_workers=a.workers) as ex:
        for rec in ex.map(compare_pair, *zip(*pairs)) if pairs else []:
            counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
            out.write(json.dumps(rec) + "\n")
    os.replace(tmp, a.out)
    print(json.dumps({"net": a.net, "sta": a.sta, "year": a.year,
                      "files": len(pairs), "verdicts": counts}))


if __name__ == "__main__":
    sys.exit(main())

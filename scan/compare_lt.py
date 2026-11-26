#!/usr/bin/env python3
"""compare_lt — staged SDS vs long-term SDS, per day-file, by exact sample time
and value (Codex 2026-11-18 F1/F2, 2026-11-24 compare review F1-F3, F5).

For every staged day-file of one station-year, the long-term (LT) file at the
same SDS path is compared. Each file is read ONCE; the verdict's sha256 is of
exactly the bytes that were decoded.

Per file, every trace must carry the stream identity its SDS path names
(NET.STA.LOC.CHA) and every sample must fall inside that SDS day; otherwise
the file is a `conflict` (reason given). Samples are grouped by exact sample
rate; rates overlapping in time are a `conflict`. Within a file, one
timestamp carrying two DIFFERENT values is a `conflict` (ambiguous source);
exact repeated (time, value) pairs collapse. Timestamps are
t0 + round(i * 1e9 / rate) in exact rational arithmetic.

  write      LT has no file at this path
  identical  same rates, same unique (time, value) samples
  superset   for every LT rate group, every LT sample exists in staging at the
             same rate, time and value, and staging holds strictly more
  conflict   anything else (missing or differing LT sample, wrong identity,
             out-of-day samples, rates overlapping in time, ambiguous duplicates)
  unreadable a file could not be decoded

Traces are never merged; a different trace split of the same samples is still
`identical`. Read-only. Output: JSON lines, each with the comparator's own
sha256 (`comparator_sha256`).

Usage: compare_lt.py --staging-root S --lt-root L --net VW --sta LOCU \
           --year 2016 [--workers 8] --out results.jsonl
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction
from pathlib import Path

import numpy as np

NS = 1_000_000_000
SDS_NAME = re.compile(r"^(?P<id>[^.]+\.[^.]*\.[^.]*\.[^.]+)\.D\.(?P<y>\d{4})\.(?P<j>\d{3})$")
COMPARATOR_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _snapshot(path):
    """(bytes, sha256) from one read."""
    b = Path(path).read_bytes()
    return b, hashlib.sha256(b).hexdigest()


def _times(t0_ns, n, rate):
    """Exact sample times t0 + round(i * 1e9 / rate), rational arithmetic."""
    fr = Fraction(rate).limit_denominator(10_000)
    num, den = fr.numerator, fr.denominator           # rate = num/den samples/s
    if n * NS * den < 2 ** 62:
        i = np.arange(n, dtype=np.int64)
        return t0_ns + (i * (NS * den) + num // 2) // num
    return np.array([t0_ns + (k * NS * den + num // 2) // num for k in range(n)], dtype=np.int64)


def _canon(blob, path):
    """{rate: (times, values)} canonical per rate, or raise ValueError(reason)."""
    from obspy import read
    from obspy import UTCDateTime
    m = SDS_NAME.match(Path(path).name)
    if not m:
        raise ValueError(f"name: not an SDS day-file name: {Path(path).name}")
    want_id = m["id"]
    day0 = UTCDateTime(year=int(m["y"]), julday=int(m["j"])).ns
    day1 = day0 + 86400 * NS
    groups, spans = {}, {}
    for tr in read(io.BytesIO(blob), format="MSEED"):
        if tr.id != want_id:
            raise ValueError(f"identity: trace {tr.id} in file of {want_id}")
        rate = float(tr.stats.sampling_rate)
        if rate <= 0 or len(tr.data) == 0:
            continue
        t = _times(int(tr.stats.starttime.ns), len(tr.data), rate)
        if t[0] < day0 or t[-1] >= day1:
            raise ValueError(f"out_of_day: samples outside {m['y']}.{m['j']}")
        groups.setdefault(rate, []).append((t, np.asarray(tr.data, dtype=np.int64)))
        spans.setdefault(rate, []).append((int(t[0]), int(t[-1])))
    rates = sorted(spans)
    for a in range(len(rates)):
        for b in range(a + 1, len(rates)):
            for s0, s1 in spans[rates[a]]:
                for u0, u1 in spans[rates[b]]:
                    if s0 <= u1 and u0 <= s1:
                        raise ValueError(f"rate_overlap: {rates[a]} and {rates[b]} Hz overlap in time")
    out = {}
    for rate, parts in groups.items():
        t = np.concatenate([p[0] for p in parts])
        v = np.concatenate([p[1] for p in parts])
        if len(t) > 1 and not bool(np.all(t[1:] > t[:-1])):
            o = np.lexsort((v, t))
            t, v = t[o], v[o]
            keep = np.ones(len(t), dtype=bool)
            keep[1:] = (t[1:] != t[:-1]) | (v[1:] != v[:-1])   # collapse exact repeats
            t, v = t[keep], v[keep]
            if np.any(t[1:] == t[:-1]):
                k = int(np.flatnonzero(t[1:] == t[:-1])[0])
                raise ValueError(f"ambiguous_duplicate: two values at {int(t[k])} ns ({rate} Hz)")
        out[rate] = (t, v)
    return out


def compare_pair(staged, lt):
    """Verdict dict for one staged day-file against its LT counterpart."""
    rec = {"staged": str(staged), "lt": str(lt), "comparator_sha256": COMPARATOR_SHA}
    sb, rec["staged_sha256"] = _snapshot(staged)
    canon = {}
    # The staged file is validated first, whether or not LT has a file: only a
    # valid staged file may ever be written (Codex 2026-11-26 F1).
    try:
        canon["staged"] = _canon(sb, staged)
    except ValueError as e:
        rec.update(verdict="conflict", reason=f"staged {e}")
        return rec
    except Exception as e:
        rec.update(verdict="unreadable", error=f"staged {type(e).__name__}: {e}"[:300])
        return rec
    if not os.path.exists(lt):
        rec["verdict"] = "write"
        rec["n_staged"] = int(sum(len(t) for t, _ in canon["staged"].values()))
        return rec
    lb, rec["lt_sha256"] = _snapshot(lt)
    for side, blob, path in (("lt", lb, lt),):
        try:
            canon[side] = _canon(blob, path)
        except ValueError as e:
            rec.update(verdict="conflict", reason=f"{side} {e}")
            return rec
        except Exception as e:
            rec.update(verdict="unreadable", error=f"{side} {type(e).__name__}: {e}"[:300])
            return rec
    S, L = canon["staged"], canon["lt"]
    rec["n_staged"] = int(sum(len(t) for t, _ in S.values()))
    rec["n_lt"] = int(sum(len(t) for t, _ in L.values()))
    rec["rates_staged"], rec["rates_lt"] = sorted(S), sorted(L)
    missing = differ = 0
    first = None
    empty = np.zeros(0, dtype=np.int64)
    for rate, (lt_t, lt_v) in L.items():
        st_t, st_v = S.get(rate, (empty, empty))
        pos = np.searchsorted(st_t, lt_t)
        has = pos < len(st_t)
        has[has] = st_t[pos[has]] == lt_t[has]
        same = np.zeros(len(lt_t), dtype=bool)
        same[has] = st_v[pos[has]] == lt_v[has]
        missing += int((~has).sum())
        differ += int((has & ~same).sum())
        bad = np.flatnonzero(~same)
        if len(bad) and first is None:
            first = int(lt_t[bad[0]])
    if missing or differ:
        rec.update(verdict="conflict", reason="lt samples absent or different in staging",
                   lt_samples_not_in_staging=missing, lt_samples_value_differs=differ,
                   first_conflict_ns=first)
        return rec
    same_shape = rec["n_staged"] == rec["n_lt"] and sorted(S) == sorted(L)
    rec["verdict"] = "identical" if same_shape else "superset"
    rec["staged_extra_samples"] = rec["n_staged"] - rec["n_lt"]
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
        for rec in (ex.map(compare_pair, *zip(*pairs)) if pairs else []):
            counts[rec["verdict"]] = counts.get(rec["verdict"], 0) + 1
            out.write(json.dumps(rec) + "\n")
    os.replace(tmp, a.out)
    print(json.dumps({"net": a.net, "sta": a.sta, "year": a.year, "files": len(pairs),
                      "verdicts": counts, "comparator_sha256": COMPARATOR_SHA}))


if __name__ == "__main__":
    sys.exit(main())

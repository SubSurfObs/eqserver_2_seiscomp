#!/usr/bin/env python3
"""test_compare_lt — verdicts of compare_lt on synthetic SDS day-files,
including the dangerous replacement cases of Codex 2026-11-24 (F1-F3, F5)."""
import sys
import tempfile
from pathlib import Path

import numpy as np
from obspy import Stream, Trace, UTCDateTime

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_lt  # noqa: E402
from compare_lt import compare_pair  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAIL.append(name)
        if detail:
            print(f"      {detail}")


T0 = UTCDateTime(2019, 6, 15)
NAME = "VW.X.00.HHZ.D.2019.166"


def write(d, pieces, rate=100.0, cid=("VW", "X", "00", "HHZ")):
    """pieces: [(seconds_from_day_start, data)]; written as separate traces."""
    path = Path(d) / NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    st = Stream()
    for start, data in pieces:
        st += Trace(np.asarray(data, dtype=np.int32),
                    header={"network": cid[0], "station": cid[1], "location": cid[2],
                            "channel": cid[3], "sampling_rate": rate, "starttime": T0 + start})
    st.write(str(path), format="MSEED", encoding="STEIM2", reclen=512)
    return path


def v(r):
    return r["verdict"]


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        full = np.arange(1000)
        S = write(td / "s", [(0, full)])
        check("identical", v(compare_pair(S, write(td / "a", [(0, full)]))) == "identical")
        check("identical across a different trace split",
              v(compare_pair(S, write(td / "b", [(0, full[:400]), (4.0, full[400:])]))) == "identical")
        r = compare_pair(S, write(td / "c", [(0, full[:600])]))
        check("superset when LT is a prefix", v(r) == "superset" and r["staged_extra_samples"] == 400, str(r))
        bad = full.copy(); bad[10] = -5
        r = compare_pair(S, write(td / "d", [(0, bad)]))
        check("conflict when one LT value differs", v(r) == "conflict" and r["lt_samples_value_differs"] == 1, str(r))
        r = compare_pair(S, write(td / "e", [(20.0, np.arange(50))]))
        check("conflict when LT has time staging lacks", v(r) == "conflict" and r["lt_samples_not_in_staging"] == 50, str(r))
        r = compare_pair(S, write(td / "f", [(0.004, full[:500])]))
        check("off-grid LT samples are never 'the same time'", v(r) == "conflict", str(r))
        r = compare_pair(S, td / "absent" / NAME)
        check("write when LT absent", v(r) == "write" and len(r["staged_sha256"]) == 64)
        # --- F1 duplicates
        r = compare_pair(S, write(td / "g", [(0, full), (0, full[:100])]))
        check("LT exact duplicates collapse: identical", v(r) == "identical", str(r))
        S2 = write(td / "s2", [(0, full), (0, full[:100])])
        check("staged exact duplicates collapse: identical",
              v(compare_pair(S2, write(td / "h", [(0, full)]))) == "identical")
        S3 = write(td / "s3", [(0, full), (0, full[:100] + 7)])
        r = compare_pair(S3, write(td / "i", [(0, full)]))
        check("staged differing duplicates: conflict", v(r) == "conflict" and "ambiguous" in r["reason"], str(r))
        r = compare_pair(S, write(td / "j", [(0, full), (0, full[:100] + 7)]))
        check("LT differing duplicates: conflict", v(r) == "conflict" and "ambiguous" in r["reason"], str(r))
        # equal-length multisets that differ: staged has dup values elsewhere
        S4 = write(td / "s4", [(0, full[:500]), (0, full[:500])])
        r = compare_pair(S4, write(td / "k", [(0, full[:1000])]))
        check("equal raw counts but staged lacks LT times: conflict", v(r) == "conflict", str(r))
        # --- F2 identity and rate
        r = compare_pair(S, write(td / "l", [(0, full)], cid=("VW", "X", "01", "HHZ")))
        check("wrong location in LT file: conflict", v(r) == "conflict" and "identity" in r["reason"], str(r))
        r = compare_pair(S, write(td / "m", [(0, full)], cid=("VX", "X", "00", "HHZ")))
        check("wrong network: conflict", v(r) == "conflict" and "identity" in r["reason"], str(r))
        r = compare_pair(S, write(td / "n", [(0, full)], rate=200.0))
        check("different rate, same first sample: conflict", v(r) == "conflict", str(r))
        L = Path(td / "o") / NAME
        L.parent.mkdir(parents=True)
        Stream([Trace(np.arange(100, dtype=np.int32), header={"network": "VW", "station": "X", "location": "00",
               "channel": "HHZ", "sampling_rate": 100.0, "starttime": T0}),
               Trace(np.arange(100, dtype=np.int32), header={"network": "VW", "station": "X", "location": "00",
               "channel": "HHZ", "sampling_rate": 250.0, "starttime": T0 + 0.5})]).write(str(L), format="MSEED", reclen=512)
        r = compare_pair(S, L)
        check("rates overlapping in time: conflict", v(r) == "conflict" and "rate_overlap" in r["reason"], str(r))
        r = compare_pair(S, write(td / "p", [(-1.0, np.arange(50))]))
        check("samples outside the SDS day: conflict", v(r) == "conflict" and "out_of_day" in r["reason"], str(r))
        # --- F3 hashes are of the decoded bytes; stale verdict detectable
        L2 = write(td / "q", [(0, full[:600])])
        r1 = compare_pair(S, L2)
        write(td / "q", [(0, full[:700])])
        r2 = compare_pair(S, L2)
        check("changed LT bytes change the recorded hash",
              r1["lt_sha256"] != r2["lt_sha256"] and r2["staged_extra_samples"] == 300)
        check("comparator identity recorded", len(r1["comparator_sha256"]) == 64)
        # --- F5 non-divisor rate: times exact across different splits
        rate = 40.0 / 3.0                       # 13.333 Hz: period not a whole ns
        a = write(td / "r", [(0, np.arange(300))], rate=rate)
        b = write(td / "t", [(0, np.arange(120)), (120 / rate, np.arange(120, 300))], rate=rate)
        r = compare_pair(a, b)
        check("non-divisor rate, different split: identical", v(r) == "identical", str(r))
        t = compare_lt._times(0, 4, rate)
        check("rational sample times", list(t) == [0, 75000000, 150000000, 225000000], str(t))
    print("all compare_lt tests passed" if not FAIL else f"{len(FAIL)} FAILED")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

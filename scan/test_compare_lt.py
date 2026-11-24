#!/usr/bin/env python3
"""test_compare_lt — verdicts of compare_lt on synthetic day-files."""
import sys
import tempfile
from pathlib import Path

import numpy as np
from obspy import Stream, Trace, UTCDateTime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_lt import compare_pair  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAIL.append(name)
        if detail:
            print(f"      {detail}")


T0 = UTCDateTime(2019, 6, 15)


def write(path, pieces, rate=100.0):
    st = Stream()
    for start, data in pieces:
        st += Trace(np.asarray(data, dtype=np.int32),
                    header={"network": "VW", "station": "X", "location": "00",
                            "channel": "HHZ", "sampling_rate": rate, "starttime": T0 + start})
    path.parent.mkdir(parents=True, exist_ok=True)
    st.write(str(path), format="MSEED", encoding="STEIM2", reclen=512)


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        full = np.arange(1000)
        write(td / "s1", [(0, full)])
        write(td / "l1", [(0, full)])
        check("identical", compare_pair(td / "s1", td / "l1")["verdict"] == "identical")
        # same samples, different record layout (re-encoded): still identical
        write(td / "l1b", [(0, full[:400]), (4.0, full[400:])])
        check("identical across a different trace split", compare_pair(td / "s1", td / "l1b")["verdict"] == "identical")
        write(td / "l2", [(0, full[:600])])
        r = compare_pair(td / "s1", td / "l2")
        check("superset when LT is a prefix", r["verdict"] == "superset" and r["staged_extra_samples"] == 400, str(r))
        bad = full.copy(); bad[10] = -5
        write(td / "l3", [(0, bad)])
        r = compare_pair(td / "s1", td / "l3")
        check("conflict when one LT value differs", r["verdict"] == "conflict" and r["lt_samples_value_differs"] == 1, str(r))
        write(td / "l4", [(20.0, np.arange(50))])
        r = compare_pair(td / "s1", td / "l4")
        check("conflict when LT has time staging lacks", r["verdict"] == "conflict" and r["lt_samples_not_in_staging"] == 50, str(r))
        write(td / "l5", [(0.004, full[:500])])   # off-grid by 0.4 sample
        r = compare_pair(td / "s1", td / "l5")
        check("off-grid LT samples are never 'the same time'", r["verdict"] == "conflict", str(r))
        r = compare_pair(td / "s1", td / "absent")
        check("write when LT absent", r["verdict"] == "write" and "staged_sha256" in r)
        r = compare_pair(td / "s1", td / "l2")
        check("verdict carries both file hashes", len(r.get("staged_sha256", "")) == 64 and len(r.get("lt_sha256", "")) == 64)
    print("all compare_lt tests passed" if not FAIL else f"{len(FAIL)} FAILED")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

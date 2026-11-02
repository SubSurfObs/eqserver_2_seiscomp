#!/usr/bin/env python3
"""test_header_names — passthrough plans keep miniSEED header channel codes
exactly (Codex 179cd8d). Builds real miniSEED files with ObsPy (run on the
VM) and reads them through the production per-file adapter.

  EHZ@100 stays EHZ;  HHO and HQO at 100 Hz stay DISTINCT (LKHRT 2023 merged
  them);  SUM@250 stays SUM;  accelerometer HNZ stays HNZ;  PLYP's HHZ@500
  stays HHZ.  Non-passthrough (VW legacy) still normalises DLZ@250 -> CHZ.
"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from obspy import Stream, Trace, UTCDateTime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase3_driver import _read_file_traces  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAIL.append(name)
        if detail:
            print(f"      {detail}")


def mseed_zip(td, name, chans):
    st = Stream()
    for ch, rate in chans:
        st += Trace(np.arange(int(rate) * 60, dtype=np.int32),
                    header={"network": "DU", "station": "FAKE", "location": "00",
                            "channel": ch, "sampling_rate": rate,
                            "starttime": UTCDateTime(2023, 7, 28)})
    buf = io.BytesIO()
    st.write(buf, format="MSEED", encoding="STEIM2", reclen=512)
    p = Path(td) / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(name.replace(".zip", ""), buf.getvalue())
    return str(p)


def names(path, keep):
    errs = []
    st = _read_file_traces(path, "mseed", "DU", "FAKE", "passthrough", None, errs,
                           keep_header_names=keep)
    return sorted({tr.stats.channel for tr in st}), errs


def main():
    with tempfile.TemporaryDirectory() as td:
        cases = [
            ("EHZ@100 stays EHZ", [("EHZ", 100.0)], ["EHZ"]),
            ("HHO and HQO stay distinct", [("HHO", 100.0), ("HQO", 100.0)], ["HHO", "HQO"]),
            ("SUM@250 stays SUM", [("SUM", 250.0)], ["SUM"]),
            ("accelerometer HNZ stays HNZ", [("HNZ", 100.0), ("HHZ", 100.0)], ["HHZ", "HNZ"]),
            ("HHZ@500 stays HHZ (PLYP)", [("HHZ", 500.0)], ["HHZ"]),
        ]
        for i, (label, chans, want) in enumerate(cases):
            got, errs = names(mseed_zip(td, f"2023-07-28_00{i:02d}_FAKE.ms.zip", chans), True)
            check(f"passthrough: {label}", got == want and not errs, f"got {got} {errs}")
        got, _ = names(mseed_zip(td, "2023-07-28_0100_FAKE.ms.zip", [("DLZ", 250.0)]), False)
        check("non-passthrough (VW legacy) still normalises DLZ@250 -> CHZ",
              got == ["CHZ"], f"got {got}")
    if FAIL:
        print(f"\n{len(FAIL)} FAILED")
        return 1
    print("\nall header-name tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
        # plain, uncompressed miniSEED (LILH 2016-17) is read, not rejected
        z = mseed_zip(td, "2016-03-10 0132 00 FAKE_HHZ.mseed.zip", [("HHZ", 100.0)])
        plain = Path(td) / "2016-03-10 0132 00 FAKE_HHZ.mseed"
        with zipfile.ZipFile(z) as zf:
            plain.write_bytes(zf.read(zf.namelist()[0]))
        got, errs = names(str(plain), True)
        check("plain uncompressed .mseed is read (LILH)", got == ["HHZ"] and not errs,
              f"got {got} {errs}")
        # WEPH: an AU trace and a DU trace, same channel and time, different
        # samples, in ONE file -> after relabelling they collide: fail loudly
        st = Stream()
        for netc, data in (("AU", np.zeros(6000, dtype=np.int32)),
                           ("DU", np.arange(6000, dtype=np.int32))):
            st += Trace(data, header={"network": netc, "station": "FAKE", "location": "0",
                                      "channel": "HHZ", "sampling_rate": 100.0,
                                      "starttime": UTCDateTime(2025, 3, 20, 8, 40)})
        buf = io.BytesIO(); st.write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        wz = Path(td) / "2025-03-20 0840 00 FAKE_HHZ.mseed.zip"
        with zipfile.ZipFile(wz, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        got, errs = names(str(wz), True)
        check("WEPH: unequal AU/DU traces collapsing to one id fail loudly",
              got == [] and any("identity collision" in str(e) for e in errs), f"{got} {errs}")
        # identical duplicate traces are NOT a collision
        st2 = Stream([st[1].copy(), st[1].copy()]); st2[0].stats.network = "AU"
        buf = io.BytesIO(); st2.write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        dz = Path(td) / "2025-03-20 0845 00 FAKE_HHZ.mseed.zip"
        with zipfile.ZipFile(dz, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        got, errs = names(str(dz), True)
        check("identical duplicate traces are not a collision", got == ["HHZ"] and not errs,
              f"{got} {errs}")
        # LKHRT: SUM is dropped by EXACT name in the chooser's reader (recorded,
        # not unknown) while its seismometer channel still reads
        import qa_verify
        sz = mseed_zip(td, "2023-07-28_0200_FAKE.ms.zip", [("HHZ", 100.0), ("SUM", 100.0)])
        ie, ic = [], {}
        orients = sorted(t["orient"] for t in qa_verify.read_source_file(sz, ie, ic))
        check("SUM dropped by exact name, not an unknown label; HHZ still read",
              orients == ["Z"] and ic.get("dropped_labels") == ["SUM"]
              and not ic.get("unknown_labels") and not ie, f"{orients} {ic} {ie}")
        uz = mseed_zip(td, "2023-07-28_0300_FAKE.ms.zip", [("HHZ", 100.0), ("SUX", 100.0)])
        ie, ic = [], {}
        qa_verify.read_source_file(uz, ie, ic)
        check("a different unknown code (SUX) still counts as unknown",
              ic.get("unknown_labels") == ["SUX"], str(ic))
        # Codex 8e7aa3d (1): two UNEQUAL source LOCATIONS that a fixed plan
        # location maps to ONE final id must fail (checked after mapping)
        lz = Path(td) / "2023-07-28_0400_FAKE.ms.zip"
        st3 = Stream()
        for lc, data in (("00", np.arange(6000, dtype=np.int32)),
                         ("01", np.arange(6000, dtype=np.int32) * 2)):
            st3 += Trace(data, header={"network": "DU", "station": "FAKE", "location": lc,
                                       "channel": "HHZ", "sampling_rate": 100.0,
                                       "starttime": UTCDateTime(2023, 7, 28, 4)})
        buf = io.BytesIO(); st3.write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(lz, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        errs = []
        got = _read_file_traces(str(lz), "mseed", "DU", "FAKE", "00", None, errs,
                                keep_header_names=True)
        check("unequal locations merged by the plan's fixed location fail loudly",
              len(got) == 0 and any("identity collision" in str(e) for e in errs), str(errs))
        # Codex 8e7aa3d (2): identical values shifted 0.49 sample = collision
        oz = Path(td) / "2023-07-28_0500_FAKE.ms.zip"
        a = Trace(np.arange(6000, dtype=np.int32),
                  header={"network": "DU", "station": "FAKE", "location": "00", "channel": "HHZ",
                          "sampling_rate": 100.0, "starttime": UTCDateTime(2023, 7, 28, 5)})
        b = a.copy(); b.stats.network = "AU"; b.stats.starttime += 0.0049
        buf = io.BytesIO(); Stream([a, b]).write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(oz, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        got, errs = names(str(oz), True)
        check("identical trace shifted 0.49 sample is a collision", got == []
              and any("identity collision" in str(e) for e in errs), f"{got} {errs}")
        # WEPH precedence: AU full minute, DU from +20 s with different data
        pz = Path(td) / "2025-03-20 0900 00 FAKE_HHZ.mseed.zip"
        au = Trace(np.zeros(6000, dtype=np.int32),
                   header={"network": "AU", "station": "FAKE", "location": "0", "channel": "HHZ",
                           "sampling_rate": 100.0, "starttime": UTCDateTime(2025, 3, 20, 9)})
        du = Trace(np.arange(1, 4001, dtype=np.int32),
                   header={"network": "DU", "station": "FAKE", "location": "0", "channel": "HHZ",
                           "sampling_rate": 100.0, "starttime": UTCDateTime(2025, 3, 20, 9, 0, 20)})
        buf = io.BytesIO(); Stream([au, du]).write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(pz, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        notes, errs = [], []
        got = _read_file_traces(str(pz), "mseed", "DU", "FAKE", "passthrough", None, errs,
                                keep_header_names=True,
                                collision_precedence={"prefer_network": "DU",
                                                      "fill_before_network": "AU"},
                                notes=notes)
        du_out = [t for t in got if t.stats.raw_network == "DU"]
        au_out = [t for t in got if t.stats.raw_network == "AU"]
        check("WEPH rule: all 4,000 DU samples kept", len(du_out) == 1
              and np.array_equal(du_out[0].data, du.data) and not errs, str(errs))
        check("WEPH rule: AU fills only the 2,000 earlier samples",
              len(au_out) == 1 and len(au_out[0].data) == 2000
              and au_out[0].stats.endtime < du.stats.starttime)
        check("WEPH rule recorded (identities + displaced count)",
              notes and "4000 displaced" in notes[0], str(notes))
        got, errs = names(str(pz), True)
        check("without the rule the same file fails loudly",
              got == [] and any("identity collision" in str(e) for e in errs), str(errs))
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

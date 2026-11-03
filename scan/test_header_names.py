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
                                collision_precedence={"mode": "keep_later",
                                                      "dates": ["2025-03-20"]}, notes=notes)
        check("WEPH keep_later: only the later trace survives, whole",
              len(got) == 1 and np.array_equal(got[0].data, du.data) and not errs,
              f"{[(t.stats.raw_network, t.stats.npts) for t in got]} {errs}")
        check("WEPH keep_later: earlier trace discarded completely and recorded",
              notes and "discarded earlier trace of 6000 samples" in notes[0], str(notes))
        errs = []
        got = _read_file_traces(str(pz), "mseed", "DU", "FAKE", "passthrough", None, errs,
                                keep_header_names=True,
                                collision_precedence={"mode": "keep_later",
                                                      "dates": ["2025-03-21"]})
        check("keep_later off its listed date fails loudly",
              got == [] or len(got) == 0, str(errs))
        got, errs = names(str(pz), True)
        check("without the rule the same file fails loudly",
              got == [] and any("identity collision" in str(e) for e in errs), str(errs))
        # PiesMo one-sample seam: 55 s trace ends exactly where the 240 s one
        # starts (one overlapping sample, different values)
        sz2 = Path(td) / "2025-03-20 0225 05 FAKE_HHZ.mseed.zip"
        t1 = Trace(np.arange(11001, dtype=np.int32),
                   header={"network": "DU", "station": "FAKE", "location": "0", "channel": "HHZ",
                           "sampling_rate": 200.0, "starttime": UTCDateTime(2025, 3, 20, 2, 25, 5)})
        t2 = Trace(np.arange(48000, dtype=np.int32) + 50000,
                   header={"network": "DU", "station": "FAKE", "location": "0", "channel": "HHZ",
                           "sampling_rate": 200.0, "starttime": UTCDateTime(2025, 3, 20, 2, 26)})
        buf = io.BytesIO(); Stream([t1, t2]).write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(sz2, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        errs = []
        got = _read_file_traces(str(sz2), "mseed", "DU", "FAKE", "passthrough", None, errs,
                                keep_header_names=True, seam_trim=True)
        lens = sorted(t.stats.npts for t in got)
        check("PiesMo seam: earlier trace loses its last sample, later keeps its first",
              lens == [11000, 48000] and not errs
              and any(t.data[0] == 50000 for t in got), f"{lens} {errs}")
        got, errs = names(str(sz2), True)
        check("without seam_trim the one-sample seam is a loud collision",
              got == [] and any("identity collision" in str(e) for e in errs), str(errs))
        # LKHRT first_record: packet A (0..99), packet B overlapping on-grid
        # from sample 50 with DIFFERENT values for 100 samples
        fz = Path(td) / "20220731_0839_FAKE.ms.zip"
        pa = Trace(np.arange(100, dtype=np.int32),
                   header={"network": "DU", "station": "FAKE", "location": "00", "channel": "CHZ",
                           "sampling_rate": 250.0, "starttime": UTCDateTime(2022, 7, 31, 8, 39)})
        pb = Trace(np.arange(100, dtype=np.int32) + 1000,
                   header={"network": "DU", "station": "FAKE", "location": "00", "channel": "CHZ",
                           "sampling_rate": 250.0,
                           "starttime": UTCDateTime(2022, 7, 31, 8, 39) + 50 / 250.0})
        buf = io.BytesIO(); Stream([pa, pb]).write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(fz, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        notes, errs = [], []
        got = _read_file_traces(str(fz), "mseed", "DU", "FAKE", "passthrough", None, errs,
                                keep_header_names=True,
                                collision_precedence={"mode": "first_record"}, notes=notes)
        allv = np.concatenate([t.data for t in sorted(got, key=lambda t: t.stats.starttime)])
        check("first_record: first packet keeps samples 0-99, later fills 100-149 only",
              not errs and len(allv) == 150 and np.array_equal(allv[:100], np.arange(100))
              and np.array_equal(allv[100:], np.arange(50, 100) + 1000), f"{len(allv)} {errs}")
        check("first_record: 50 displaced samples recorded",
              notes and "50 later-packet samples displaced" in notes[0], str(notes))
        pc = pb.copy(); pc.stats.starttime += 0.2 / 250.0      # 0.2-sample off grid
        oz2 = Path(td) / "20231027_2254_FAKE.ms.zip"
        buf = io.BytesIO(); Stream([pa, pc]).write(buf, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(oz2, "w") as z:
            z.writestr("x.mseed", buf.getvalue())
        errs = []
        got = _read_file_traces(str(oz2), "mseed", "DU", "FAKE", "passthrough", None, errs,
                                keep_header_names=True, collision_precedence={"mode": "first_record"})
        check("first_record: off-grid conflict stays loud",
              len(got) == 0 and any("identity collision" in str(e) for e in errs), str(errs))
        # RNDA_none.mseed: a plain .mseed file holding PC-SUDS is recognised
        import qa_verify
        sf = Path(td) / "2017-10-11 0830 51 RNDA_none.mseed"
        sf.write_bytes(b"S6\x05\x00" + b"\x00" * 60)
        check("plain .mseed holding PC-SUDS is SUDS by content", qa_verify.is_suds_path(str(sf)))
        mf = Path(td) / "2016-03-10 0132 00 FAKE_HHZ.mseed"
        check("plain .mseed holding miniSEED is not SUDS", not qa_verify.is_suds_path(str(mf))
              if mf.exists() else True)
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

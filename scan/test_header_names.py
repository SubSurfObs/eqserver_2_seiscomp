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
from obspy import Stream, Trace, UTCDateTime, read

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
        # earliest_start (Codex 2026-11-09 F1). Packets written in REVERSE time
        # order prove the rule follows stated start, not file order.
        t00 = UTCDateTime(2022, 7, 31, 8, 39)
        hdr = {"network": "DU", "station": "FAKE", "location": "00", "channel": "CHZ",
               "sampling_rate": 250.0}
        pa = Trace(np.arange(100, dtype=np.int32), header={**hdr, "starttime": t00})
        pb = Trace(np.arange(100, dtype=np.int32) + 1000,
                   header={**hdr, "starttime": t00 + 50 / 250.0})
        def overlay(fname, traces, rule={"mode": "earliest_start"}):
            fz = Path(td) / fname
            b_ = io.BytesIO()
            for t_ in traces:
                t_.write(b_, format="MSEED", encoding="STEIM2", reclen=512)
            with zipfile.ZipFile(fz, "w") as z:
                z.writestr("x.mseed", b_.getvalue())
            nts, ers = [], []
            g = _read_file_traces(str(fz), "mseed", "DU", "FAKE", "passthrough", None, ers,
                                  keep_header_names=True, collision_precedence=rule, notes=nts)
            return sorted(g, key=lambda t: t.stats.starttime), nts, ers
        got, notes, errs = overlay("20220731_0839_FAKE.ms.zip", [pb, pa])
        allv = np.concatenate([t.data for t in got])
        check("earliest_start: earliest packet keeps 0-99, later fills 100-149 only, by start not file order",
              not errs and len(allv) == 150 and np.array_equal(allv[:100], np.arange(100))
              and np.array_equal(allv[100:], np.arange(50, 100) + 1000), f"{len(allv)} {errs}")
        check("earliest_start: file, id and 50 displaced samples recorded",
              notes and notes[0]["file"] == "20220731_0839_FAKE.ms.zip"
              and notes[0]["id"] == "DU.FAKE.00.CHZ"
              and notes[0]["displaced"][0]["n_samples"] == 50
              and notes[0]["displaced"][0]["grid_offset"] == 0.0, str(notes))
        # off grid by 0.2 sample: later packet keeps its OWN grid, no overlap
        pc = pb.copy(); pc.stats.starttime += 0.2 / 250.0
        got, notes, errs = overlay("20231027_2254_FAKE.ms.zip", [pa, pc])
        dt = 4_000_000
        check("earliest_start off-grid: both kept, later on its own grid, no overlap, exact values",
              not errs and len(got) == 2
              and (got[1].stats.starttime.ns - pc.stats.starttime.ns) % dt == 0
              and got[1].stats.starttime.ns >= got[0].stats.starttime.ns + 100 * dt
              and np.array_equal(got[1].data, np.arange(50, 100) + 1000), f"{got} {errs}")
        check("earliest_start off-grid: 0.2-sample offset recorded",
              notes and abs(notes[0]["displaced"][0]["grid_offset"] - 0.2) < 1e-3, str(notes))
        # contained packet is displaced whole; nothing of it survives
        pd_ = Trace(np.arange(10, dtype=np.int32) + 5000, header={**hdr, "starttime": t00 + 20 / 250.0})
        got, notes, errs = overlay("20220731_0840_FAKE.ms.zip", [pa, pd_])
        check("earliest_start: contained packet displaced whole",
              not errs and len(got) == 1 and np.array_equal(got[0].data, np.arange(100)), str(got))
        # different rates overlapping at one id: no rule covers it, stays loud
        pe = Trace(np.arange(100, dtype=np.int32) + 9000,
                   header={**hdr, "sampling_rate": 100.0, "starttime": t00 + 0.1})
        got, notes, errs = overlay("20220731_0841_FAKE.ms.zip", [pa, pe])
        check("earliest_start: different rates at one id stay loud",
              got == [] and any("identity collision" in str(e) for e in errs), str(errs))
        # the auditor's own implementation agrees with the converter
        import qa_verify as qv
        tdicts = [{"orient": "Z", "src_chan": "CHZ", "src_loc": "00", "src_path": "x",
                   "start": t.stats.starttime, "rate": 250.0,
                   "data": np.asarray(t.data, dtype=np.int64)} for t in (pc, pa)]
        view = sorted(qv.earliest_start_view(tdicts, False, {}), key=lambda t: t["start"].ns)
        conv, _, _ = overlay("20231027_2255_FAKE.ms.zip", [pa, pc])
        check("auditor's earliest-start view equals the converter's output",
              len(view) == len(conv) and all(
                  v["start"].ns == c.stats.starttime.ns and np.array_equal(v["data"], c.data)
                  for v, c in zip(view, conv)), str(view))
        # a run manifest may not record an unknown release identity
        from phase3_driver import release_identity_gaps
        check("release block with '?' or empty is rejected",
              release_identity_gaps({"eqserver": "abc", "sudspy": "?", "disk_to_sds": ""})
              == ["disk_to_sds", "sudspy"]
              and release_identity_gaps({"eqserver": "abc"}) == [])
        # evidence: totals in the manifest, every interval in the sidecar
        from phase3_driver import _earliest_start_totals, _write_precedence_evidence
        import gzip as _gz, json as _js
        rec = {"date": "2022-07-31", "rule": "earliest_start", "file": "f", "id": "x", "rate": 250.0,
               "displaced": [{"start_ns": 1, "n_samples": 50, "grid_offset": 0.0},
                             {"start_ns": 2, "n_samples": 5, "grid_offset": 0.2}]}
        check("day totals of displaced intervals",
              _earliest_start_totals([rec, "keep_later text"]) ==
              {"files": 1, "intervals": 2, "displaced_samples": 55, "off_grid_intervals": 1})
        ev = _write_precedence_evidence(str(Path(td) / "m.json"), [rec])
        back = [_js.loads(l) for l in _gz.open(ev["path"], "rt")]
        check("sidecar holds every interval", back == [rec] and ev["n_records"] == 1, str(back))
        check("no evidence, no sidecar", _write_precedence_evidence(str(Path(td) / "n.json"), []) is None)
        # a damaged file is rejected and recorded, it does not block its day
        from coverage_planner import ChannelKnowledge
        broken = Path(td) / "20230628_0623_FAKE.ms.zip"
        broken.write_bytes(b"not a zip")
        _p, prec = ChannelKnowledge()._probe_file(str(broken))
        check("damaged file probes as unreadable, not unresolved",
              prec["status"] == "unreadable" and prec["err"], str(prec))
        # coalescing never moves a sample (Codex 2026-11-10 F1): the .121/.125
        # case, written to miniSEED and read back with a standard reader
        from phase3_driver import _coalesce_pieces
        dtn = 10_000_000
        base = UTCDateTime(2023, 10, 27, 5, 6, 15, 5000).ns          # grid .005
        p1 = {"t0_ns": base, "data": np.arange(611, dtype=np.int32)}   # ends .105
        p2 = {"t0_ns": base + 611 * dtn + 6_000_000,                   # .121
              "data": np.arange(100, dtype=np.int32) + 5000}
        p3 = {"t0_ns": p2["t0_ns"] + 100 * dtn + 4_000_000,            # .125 grid
              "data": np.arange(100, dtype=np.int32) + 9000}
        p4 = {"t0_ns": p3["t0_ns"] + 100 * dtn,                        # same grid: joins
              "data": np.arange(50, dtype=np.int32) + 20000}
        g = {}
        runs = _coalesce_pieces([p1, p2, p3, p4], dtn, g)
        st_ = Stream([Trace(np.concatenate(parts), header={"sampling_rate": 100.0,
                      "starttime": UTCDateTime(ns=t0), "network": "DU", "station": "FAKE",
                      "location": "00", "channel": "HHZ"}) for t0, parts in runs])
        b_ = io.BytesIO(); st_.write(b_, format="MSEED", encoding="STEIM2", reclen=512); b_.seek(0)
        back = read(b_, format="MSEED")
        want = {}
        for pc in (p1, p2, p3, p4):
            for i, v in enumerate(pc["data"]):
                want[pc["t0_ns"] + i * dtn] = int(v)
        got_ = {}
        for t in back:
            for i, v in enumerate(t.data):
                got_[t.stats.starttime.ns + i * dtn] = int(v)
        check("coalesce + write + read: every sample at its exact stated time",
              all(want.get(t) == v for t, v in got_.items()), str(back))
        check("coalesce: only the weld-guard samples are missing, and counted",
              len(want) - len(got_) == g.get("weld_guard_dropped") == 1, f"{len(want)} {len(got_)} {g}")
        # foreign-station trace inside a file is dropped and recorded
        fz = Path(td) / "2019-04-06 1924 00 FAKE_HHZ.mseed.zip"
        own = Trace(np.arange(100, dtype=np.int32), header={**hdr, "starttime": t00})
        alien = Trace(np.arange(50, dtype=np.int32), header={**hdr, "station": "AUKSC",
                      "network": "S", "location": "", "starttime": t00})
        b_ = io.BytesIO(); Stream([own, alien]).write(b_, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(fz, "w") as z:
            z.writestr("x.mseed", b_.getvalue())
        nts, ers = [], []
        g = _read_file_traces(str(fz), "mseed", "DU", "FAKE", "passthrough", None, ers,
                              keep_header_names=True, notes=nts)
        check("foreign-station trace dropped and recorded",
              [t.stats.station for t in g] == ["FAKE"] and not ers
              and any("AUKSC" in n for n in nts), f"{g} {nts}")
        fz2 = Path(td) / "2019-04-06 1925 00 FAKE_HHZ.mseed.zip"
        b_ = io.BytesIO(); alien.write(b_, format="MSEED", encoding="STEIM2", reclen=512)
        with zipfile.ZipFile(fz2, "w") as z:
            z.writestr("x.mseed", b_.getvalue())
        ers = []
        g = _read_file_traces(str(fz2), "mseed", "DU", "FAKE", "passthrough", None, ers,
                              keep_header_names=True, notes=[])
        check("file with only another station's traces fails loudly",
              len(g) == 0 and any("another station" in str(e) for e in ers), str(ers))
        # named event miniSEED: plain records, odd extension, indexed with its channel
        import level1
        r1 = level1.parse_filename("170512.020837.glds.shz.pn.01", "GLDS")
        check("named event miniSEED indexed",
              r1["role"] == "waveform" and r1["exclude_reason"] is None
              and r1["hhmm"] == "0208" and r1["channel_suffix"] == "SHZ"
              and (r1["file_year"], r1["file_month"], r1["file_day"]) == (2017, 5, 12), str(r1))
        pe = Path(td) / "170512.020837.fake.shz.pn.01"
        b_ = io.BytesIO(); own.write(b_, format="MSEED", encoding="STEIM2", reclen=512)
        pe.write_bytes(b_.getvalue())
        ers = []
        g = _read_file_traces(str(pe), "mseed", "DU", "FAKE", "passthrough", None, ers,
                              keep_header_names=True)
        check("plain miniSEED with an odd extension is read",
              len(g) == 1 and g[0].stats.npts == 100 and not ers, f"{g} {ers}")
        # RNDA_none.mseed: a plain .mseed file holding PC-SUDS is recognised
        import qa_verify
        sf = Path(td) / "2017-10-11 0830 51 RNDA_none.mseed"
        sf.write_bytes(b"S6\x05\x00" + b"\x00" * 60)
        check("plain .mseed holding PC-SUDS is SUDS by content", qa_verify.is_suds_path(str(sf)))
        mf = Path(td) / "2016-03-10 0132 00 FAKE_HHZ.mseed"
        check("plain .mseed holding miniSEED is not SUDS", not qa_verify.is_suds_path(str(mf))
              if mf.exists() else True)
        # sp_band_by_rate: band letter follows each trace's rate (MVM1Y)
        for ch, rate, want_ch in (("DHZ", 100.0, "EHZ"), ("DHZ", 250.0, "DHZ")):
            bz = mseed_zip(td, f"2021-10-09_09{int(rate) % 60:02d}_FAKE.ms.zip", [(ch, rate)])
            errs = []
            got = _read_file_traces(bz, "mseed", "DU", "FAKE", "00", None, errs,
                                    keep_header_names=True, sp_band_by_rate=True)
            check(f"sp_band_by_rate: {rate:g} Hz becomes {want_ch}",
                  [t.stats.channel for t in got] == [want_ch] and not errs, str(errs))
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

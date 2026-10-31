#!/usr/bin/env python3
"""test_identity_fixes — the wave-2 blocked-identity causes (Codex live
scrutiny e0f38dc; 2026-10-31). Needs obspy + sudspy (run on the VM).

1. Echo label spellings: "Up" (RNDA), "Up T"/"East T"/"North T" (BRTS,
   PLMR) map to Z/E/N; accelerometer "-A" and unknown labels do not.
2. WKA class: a population whose sampled probes all read cleanly but
   EMPTY no longer blocks the day — its files are judged one by one; the
   other population converts; truly empty files are recorded as rejected.
3. ...but a file with an UNRECOGNISED label in that population still
   blocks its day (never a silent drop).
4. Bare '.gz' SUDS (HKER 2017) is recognised as SUDS by content.
"""
import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_verify  # noqa: E402
from coverage_planner import ChannelKnowledge, choose_day  # noqa: E402

FAILURES = []
MK = "2025-03"


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)
        if detail:
            print(f"      {detail}")


def cand(pop, hhmm, source):
    sep = "_" if source == "disk" else " "
    return {"path": f"/x/{pop}/2025-03-15{sep}{hhmm}{sep}49{sep}WKA.dmx",
            "source": source, "hhmm": hhmm, "ss": "49", "flags": None,
            "size": 11000 if source == "telemetry" else 2700,
            "chan_suffix": None, "population": pop}


def run_day(fake_reader):
    real = qa_verify.read_source_file
    qa_verify.read_source_file = fake_reader
    try:
        know = ChannelKnowledge(probe_enabled=True)
        tele = [cand("telemetry:space4", f"{h:02d}{m:02d}", "telemetry")
                for h in range(24) for m in range(60)]
        disk = [cand("disk:underscore4", hm, "disk")
                for hm in ("1029", "1210", "2125", "2246")]
        for c in tele + disk:
            know.observe(c, MK, 15)
        know.probe_all()
        sel, rej, _g, blocked = choose_day(tele + disk, know, MK)
        return tele, disk, sel, rej, blocked
    finally:
        qa_verify.read_source_file = real


def main():
    # 1. labels
    want = {"Up": "Z", "Up T": "Z", "Up-T": "Z", "East T": "E",
            "North T": "N", "c03": "Z", "c01": "N",
            "v": "Z", "v Trans": "Z"}
    for lab, o in want.items():
        check(f"label {lab!r} -> {o}", qa_verify._suds_component(lab) == o,
              str(qa_verify._suds_component(lab)))
    for lab in ("Up-A", "Up A", "v Acc", "v Accel"):
        check(f"label {lab!r} is a RECOGNISED drop (None)",
              qa_verify._suds_component(lab) is None,
              str(qa_verify._suds_component(lab)))
    for lab in ("w",):
        got = qa_verify._suds_component(lab)
        check(f"label {lab!r} not a seismometer channel",
              got is None or got.startswith("?"), str(got))

    # 2. empty disk files beside full telemetry
    def reader_empty_disk(p, errors, counters):
        if "_" in Path(p).name:
            return []
        return [{"orient": "Z", "src_chan": "c03"}]
    tele, disk, sel, rej, blocked = run_day(reader_empty_disk)
    check("empty-probe population does not block the day", not blocked,
          str(blocked))
    check("telemetry converts (Z)",
          all("Z" in (sel.get(c["path"]) or {}).get("chans", ())
              for c in tele[:5]))
    check("empty disk files recorded as rejected, not selected",
          {r["path"] for r in rej} >= {c["path"] for c in disk}
          and not any(c["path"] in sel for c in disk))

    # 3. an unrecognised label in that population still blocks
    def reader_unknown(p, errors, counters):
        if Path(p).name.startswith("2025-03-15_2125"):
            # what read_source_file really returns for an unknown SUDS label
            return [{"orient": "?w", "src_chan": "w"}]
        if Path(p).name.startswith("2025-03-15_2246"):
            # unknown mseed channel (no orientation letter)
            counters.setdefault("unknown_labels", []).append("HHX")
            return []
        return reader_empty_disk(p, errors, counters)
    *_x, blocked = run_day(reader_unknown)
    check("unrecognised label still blocks its day",
          any(b == "file_unresolved" for _p, b in blocked), str(blocked))

    # 3b. accelerometer-only files are skipped, not blocking (Codex e41bebb)
    def reader_accel(p, errors, counters):
        if "_" in Path(p).name:
            counters.setdefault("dropped_labels", []).append("v Acc")
            return []
        return [{"orient": "Z", "src_chan": "v"}]
    tele, disk, sel, rej, blocked = run_day(reader_accel)
    check("accelerometer-only files skipped and recorded, day not blocked",
          not blocked and {r["path"] for r in rej} >= {c["path"] for c in disk},
          str(blocked))

    # 3c. MIXED known + unknown channels in one file (Codex 0f68135):
    #     c03 + 'w' (SUDS) and HHZ + HHX (mseed) must block, at the
    #     per-file level AND when the file is one of the month's samples.
    def reader_mixed_suds(p, errors, counters):
        if Path(p).name.startswith("2025-03-15_2125"):
            return [{"orient": "Z", "src_chan": "c03"},
                    {"orient": "?w", "src_chan": "w"}]
        return reader_empty_disk(p, errors, counters)
    def reader_mixed_mseed(p, errors, counters):
        if Path(p).name.startswith("2025-03-15_2125"):
            counters.setdefault("unknown_labels", []).append("HHX")
            return [{"orient": "Z", "src_chan": "HHZ"}]
        return reader_empty_disk(p, errors, counters)
    for nm, rdr in (("SUDS c03+w", reader_mixed_suds),
                    ("mseed HHZ+HHX", reader_mixed_mseed)):
        *_x, blocked = run_day(rdr)
        check(f"mixed known+unknown ({nm}) blocks its day",
              any(b == "file_unresolved" for _p, b in blocked), str(blocked))
        real = qa_verify.read_source_file
        qa_verify.read_source_file = rdr
        try:
            know = ChannelKnowledge(probe_enabled=True)
            _p, rec = know._probe_file("/x/2025-03-15_2125_49_WKA.dmx")
        finally:
            qa_verify.read_source_file = real
        check(f"_probe_file marks {nm} unresolved, not resolved",
              rec["status"] == "unresolved", str(rec))
    # Sampled-probe path: mixed file is one of the month's SAMPLES of the
    # telemetry population -> must not be accepted as 'agreed Z'.
    def reader_mixed_sampled(p, errors, counters):
        if Path(p).name.startswith("2025-03-15 0000"):
            return [{"orient": "Z", "src_chan": "c03"},
                    {"orient": "?w", "src_chan": "w"}]
        return reader_empty_disk(p, errors, counters)
    *_x, blocked = run_day(reader_mixed_sampled)
    check("mixed known+unknown in a SAMPLED file blocks (no agreed-Z)",
          any(b == "file_unresolved" for _p, b in blocked), str(blocked))

    # 4. bare .gz SUDS sniff
    with tempfile.TemporaryDirectory() as td:
        s = Path(td) / "2017-03-15 0000 00 HKER.gz"
        s.write_bytes(gzip.compress(b"S6\x05\x00" + b"\x00" * 60))
        m = Path(td) / "2017-03-15 0000 00 HKER2.gz"
        m.write_bytes(gzip.compress(b"000001D " + b"\x00" * 60))
        check("bare .gz holding SUDS is SUDS", qa_verify.is_suds_path(str(s)))
        check("bare .gz holding mseed is not SUDS",
              not qa_verify.is_suds_path(str(m)))
        check(".dmx.gz is SUDS by extension",
              qa_verify.is_suds_path("/nonexistent/a.dmx.gz"))

    if FAILURES:
        print(f"\n{len(FAILURES)} identity-fix test(s) FAILED: {FAILURES}")
        return 1
    print("\nall identity-fix tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

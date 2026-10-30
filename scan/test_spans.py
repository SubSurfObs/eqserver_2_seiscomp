#!/usr/bin/env python3
"""test_spans — read-reach vs coverage-claim (Codex measured-span review,
2026-10-30). Drives choose_day directly; no archive access.

1. Measurement FAILED (no span evidence) on a five-minute stream: reach
   comes from filename cadence — five minutes, never one.
2. A short/gappy WINNER cannot suppress another source: the other
   population is still authorized (as fill) for the winner's unclaimed
   minutes.
3. Cadence change mid-day (PLY3 class: five-minute files then one-minute
   files): each file's reach follows its own successor.
4. A long file (100 minutes, the 6,000-second class) is honoured, not
   capped at an hour.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coverage_planner import ChannelKnowledge, choose_day  # noqa: E402

FAILURES = []
MK = "2025-06"


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)
        if detail:
            print(f"      {detail}")


def cand(pop, hhmm, source="telemetry", size=1000):
    return {"path": f"/x/{pop}/2025-06-13 {hhmm} 00 STA_HHZ.mseed.zip",
            "source": source, "hhmm": hhmm, "ss": "00", "flags": None,
            "size": size, "chan_suffix": "_HHZ", "population": pop}


def window(sel, c, orient="Z"):
    return sorted((sel.get(c["path"]) or {}).get("o_slots", {}).get(orient, []))


def mins(a, b):
    return [f"{m // 60:02d}{m % 60:02d}" for m in range(a, b + 1)]


def main():
    # 1. five-minute stream, NO measured span
    know = ChannelKnowledge(probe_enabled=False)
    cs = [cand("p", f"{h:02d}{m:02d}") for h in (0, 1) for m in range(0, 60, 5)]
    sel, _rej, _g, blocked = choose_day(cs, know, MK)
    w = window(sel, cs[1])  # 00:05 file
    check("unmeasured 5-min file reads 5 minutes (from cadence)",
          w == mins(5, 9), f"window={w}")

    # 2. gappy winner cannot suppress a real fill
    know = ChannelKnowledge(probe_enabled=False)
    A = [cand("A", hm, source="disk") for hm in ("0000", "0005", "0010")]
    B = [cand("B", hm) for hm in ("0002", "0003")]
    sel, _rej, _g, _b = choose_day(A + B, know, MK)
    wb = window(sel, B[0])
    check("winner population chosen by proven (claimed) coverage",
          window(sel, A[0]) == mins(0, 4))
    check("other source still authorized where winner proves nothing",
          "0002" in wb, f"B[0] window={wb}")

    # 3. cadence change mid-day
    know = ChannelKnowledge(probe_enabled=False)
    cs = [cand("p", hm) for hm in ("0000", "0005", "0010", "0015", "0016", "0017")]
    sel, _rej, _g, _b = choose_day(cs, know, MK)
    check("last five-minute file before the switch reads 5 minutes",
          window(sel, cs[2]) == mins(10, 14), f"{window(sel, cs[2])}")
    check("one-minute file after the switch reads its own minute",
          window(sel, cs[3]) == mins(15, 15), f"{window(sel, cs[3])}")

    # 4. a 100-minute file is honoured
    know = ChannelKnowledge(probe_enabled=False)
    know.spans[("p", MK)] = 100
    cs = [cand("p", "0000"), cand("p", "0030")]
    sel, _rej, _g, _b = choose_day(cs, know, MK)
    w = window(sel, cs[0])
    check("long measured file not capped at an hour",
          w[:1] == ["0000"] and "0139" in w, f"last={w[-1:] if w else w}")

    # 5. Re-review F2: a one-minute stream that LOOKS complete (all minutes
    #    named) must not cause an equally complete five-minute copy to be
    #    discarded — the latter may hold an internal gap's data.
    know = ChannelKnowledge(probe_enabled=False)
    P1 = [cand("p1", f"00{m:02d}", source="disk") for m in range(10)]
    P5 = [cand("p5", hm) for hm in ("0000", "0005")]
    sel, rej, _g, _b = choose_day(P1 + P5, know, MK)
    lost = [r for r in rej if r["path"] in {c["path"] for c in P5}]
    check("five-minute alternative copy is never discarded",
          not lost and window(sel, P5[0])[:5] == mins(0, 4),
          f"rejected={[r['reason'] for r in lost]} window={window(sel, P5[0])[:6]}")

    # 6. Re-review F1 (DNL3 2024-01-05 2359): the day's last file runs to the
    #    next day's first file of the same population (01:39 here).
    know = ChannelKnowledge(probe_enabled=False)
    last = cand("p", "2359")
    sel, _r, _g, _b = choose_day([cand("p", "2358"), last], know, MK,
                                 next_first={"p": 99})
    w = window(sel, last)
    check("23:59 file reads across midnight to 01:38 next day",
          "2359" in w and "2538" in w and "2539" not in w,
          f"first={w[:1]} last={w[-1:]}")

    # 7. ...and with no files at all the next day, to the end of that day.
    sel, _r, _g, _b = choose_day([last], know, MK, next_first={})
    w = window(sel, last)
    check("last file with an empty next day reads to its end",
          w[-1:] == ["4759"], f"last={w[-1:]}")

    # 8. Codex pin review (39132f7): with THREE competing copies, the
    #    2nd-ranked copy must outrank the 3rd by the compiler's ranking,
    #    not by pathname (the 3rd's path deliberately sorts first here).
    know = ChannelKnowledge(probe_enabled=False)
    A = [cand("mA", f"00{m:02d}") for m in range(0, 30)]        # best
    B = [cand("zB", f"00{m:02d}") for m in range(0, 30, 5)]     # 2nd
    C = [cand("aC", f"00{m:02d}") for m in range(0, 30, 10)]    # 3rd
    sel, _r, _g, _b = choose_day(A + B + C, know, MK, next_first={
        "mA": 0, "zB": 0, "aC": 0})
    pa = sel[A[0]["path"]].get("by_orient", {}).get("Z", "")
    pb = sel[B[0]["path"]].get("prio_by_orient", {}).get("Z")
    pc = sel[C[0]["path"]].get("prio_by_orient", {}).get("Z")
    check("winner is the most complete copy", pa.startswith("winner"), pa)
    check("2nd-ranked copy outranks 3rd regardless of path order",
          pb is not None and pc is not None and pb < pc, f"B={pb} C={pc}")

    if FAILURES:
        print(f"\n{len(FAILURES)} span test(s) FAILED: {FAILURES}")
        return 1
    print("\nall span tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

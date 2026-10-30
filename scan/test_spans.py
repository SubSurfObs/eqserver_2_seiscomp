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

    if FAILURES:
        print(f"\n{len(FAILURES)} span test(s) FAILED: {FAILURES}")
        return 1
    print("\nall span tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

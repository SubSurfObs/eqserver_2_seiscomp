#!/usr/bin/env python3
"""test_resolver — the integer-nanosecond precedence overlay.

Codex review 2026-10-28 F2 demanded: realistic 2012–2026 epochs, every
archive rate, single-segment identity, winner+fill boundary values,
fractional starts, midnight. Every case asserts sample COUNTS and
boundary VALUES, never just shapes. Pure python."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.resolver import resolve_segments, NS  # noqa: E402

FAILURES = []
RATES = [40, 50, 100, 200, 250, 400, 500, 800, 1000]
EPOCHS_NS = [
    1325376000 * NS,            # 2012-01-01
    1548288000 * NS + 123456000,  # 2019-01-24 00:00:00.123456
    1560556800 * NS,            # 2019-06-15
    1782864000 * NS + 7000,     # 2026-07-01 + 7 microseconds-ish
]


def seg(t0_ns, n, prio, path, first_val):
    return {"t0_ns": t0_ns, "rate": RATE, "priority": prio, "path": path,
            "data": list(range(first_val, first_val + n))}


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)
        if detail:
            print(f"      {detail}")


def main():
    global RATE
    # 1. Single-segment identity at every rate and epoch: in == out,
    #    exactly, including 1-sample and 12,001-sample segments.
    bad = []
    for RATE in RATES:
        dt = NS // RATE
        for e in EPOCHS_NS:
            for n in (1, 7, 12001):
                out = resolve_segments([seg(e, n, 0, "/w", 5)])
                if (len(out) != 1 or out[0]["t0_ns"] != e
                        or len(out[0]["data"]) != n
                        or out[0]["data"][0] != 5
                        or out[0]["data"][-1] != 5 + n - 1):
                    bad.append((RATE, e, n, [(o["t0_ns"], len(o["data"]))
                                             for o in out]))
    check("single-segment identity, all rates x epochs x lengths",
          not bad, f"first failures: {bad[:3]}")

    # 2. Winner + whole-minute fill at realistic epochs: winner intact,
    #    fill supplies EXACTLY the tail, first fill sample value correct.
    bad = []
    for RATE in RATES:
        dt = NS // RATE
        for e in EPOCHS_NS:
            half = 30 * RATE          # 30 s of samples
            out = resolve_segments([
                seg(e, half, 0, "/win", 1000),
                seg(e, 60 * RATE, 1, "/fill", 2000)])
            ok = (len(out) == 2
                  and out[0]["t0_ns"] == e and len(out[0]["data"]) == half
                  and out[0]["data"][-1] == 1000 + half - 1
                  and out[1]["t0_ns"] == e + half * dt
                  and len(out[1]["data"]) == 60 * RATE - half
                  and out[1]["data"][0] == 2000 + half)
            if not ok:
                bad.append((RATE, e, [(o["t0_ns"] - e, len(o["data"]),
                                       o["data"][0]) for o in out]))
    check("winner+fill boundary exact, all rates x epochs",
          not bad, f"first failures: {bad[:2]}")

    # 3. Fractional-grid fill: fill's grid is offset by 1/3 sample; its
    #    first retained sample is the first one at/after the winner's end,
    #    anchored at ITS OWN stated time.
    RATE = 250
    dt = NS // RATE
    e = EPOCHS_NS[1]
    off = dt // 3
    out = resolve_segments([seg(e, 100, 0, "/w", 0),
                            dict(seg(e + off, 200, 1, "/f", 500))])
    want_t0 = e + off + 100 * dt  # first fill sample >= winner end
    ok = (len(out) == 2 and out[1]["t0_ns"] == want_t0
          and out[1]["data"][0] == 500 + 100
          and len(out[1]["data"]) == 100)
    check("fractional-grid fill keeps stated grid", ok,
          f"got {[(o['t0_ns'] - e, len(o['data']), o['data'][0]) for o in out]}")

    # 4. Order independence at epoch scale.
    a = [seg(e, 100, 0, "/w", 0), seg(e, 200, 1, "/f", 500)]
    o1 = resolve_segments(a)
    o2 = resolve_segments(list(reversed(a)))
    same = [(s["t0_ns"], len(s["data"]), s["data"][0]) for s in o1] == \
           [(s["t0_ns"], len(s["data"]), s["data"][0]) for s in o2]
    check("order independence at epoch scale", same)

    # 5. Midnight: winner ends at 23:59:30, fill crosses into the next
    #    day; fill contributes before AND after midnight, winner intact.
    day_ns = 86400 * NS
    e0 = 1560556800 * NS  # day start
    wstart = e0 + (86400 - 90) * NS      # 23:58:30, 60 s winner
    out = resolve_segments([
        seg(wstart, 60 * RATE, 0, "/w", 0),
        seg(e0 + (86400 - 60) * NS, 120 * RATE, 2, "/f", 9000)])
    ends = [(s["t0_ns"] - e0, len(s["data"])) for s in out]
    ok = (len(out) == 2 and out[0]["t0_ns"] == wstart
          and len(out[0]["data"]) == 60 * RATE
          and out[1]["t0_ns"] == wstart + 60 * RATE * dt
          and out[1]["t0_ns"] + len(out[1]["data"]) * dt
              == e0 + (86400 + 60) * NS)
    check("midnight winner/fill handoff exact", ok, f"got {ends}")

    # 6. One-sample winner survives at every epoch (the exact case Codex
    #    proved the float version lost).
    bad = []
    for e in EPOCHS_NS:
        out = resolve_segments([seg(e, 1, 0, "/w", 77)])
        if len(out) != 1 or out[0]["data"] != [77]:
            bad.append(e)
    check("one-sample segment survives at all epochs", not bad, str(bad))

    # 7. Non-divisible rate refuses rather than approximates.
    try:
        resolve_segments([{"t0_ns": e, "rate": 3.0, "priority": 0,
                           "path": "/x", "data": [1]}])
        check("non-integer-ns rate refuses", False)
    except ValueError:
        check("non-integer-ns rate refuses", True)

    # 8. Float-seconds compatibility input converts once, loses nothing.
    out = resolve_segments([{"t0": 1548288000.1234567, "rate": 250.0,
                             "priority": 0, "path": "/w",
                             "data": list(range(12001))}])
    ok = len(out) == 1 and len(out[0]["data"]) == 12001
    check("float-t0 compatibility keeps all samples", ok)

    if FAILURES:
        print(f"\n{len(FAILURES)} resolver test(s) FAILED: {FAILURES}")
        return 1
    print("\nall resolver tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

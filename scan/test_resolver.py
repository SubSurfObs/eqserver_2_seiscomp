#!/usr/bin/env python3
"""test_resolver — the explicit-precedence overlay (Codex F5/F6 design).
Pure python; list data. Times in seconds at 100 Hz (dt = 0.01)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.resolver import resolve_segments  # noqa: E402

FAILURES = []


def seg(t0, n, prio, path, val):
    return {"t0": t0, "rate": 100.0, "data": [val] * n,
            "priority": prio, "path": path}


def check(name, got, want):
    shaped = [(round(s["t0"], 4), len(s["data"]), s["data"][0]) for s in got]
    ok = shaped == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILURES.append(name)
        print(f"      got  {shaped}\n      want {want}")


def main():
    # 1. Short winner + whole-minute fill copy: winner survives intact,
    #    fill supplies ONLY the tail, anchored at its own stated grid.
    out = resolve_segments([seg(0.0, 50, 0, "/win", 1),
                            seg(0.0, 100, 1, "/fill", 2)])
    check("short winner + fill tail", out, [(0.0, 50, 1), (0.5, 50, 2)])

    # 2. Order independence: same input reversed gives the same output.
    out2 = resolve_segments([seg(0.0, 100, 1, "/fill", 2),
                             seg(0.0, 50, 0, "/win", 1)])
    check("order independence", out2, [(0.0, 50, 1), (0.5, 50, 2)])

    # 3. Fully contained lower-priority copy contributes nothing.
    out = resolve_segments([seg(0.0, 100, 0, "/win", 1),
                            seg(0.1, 50, 1, "/dup", 2)])
    check("contained fill drops", out, [(0.0, 100, 1)])

    # 4. Drifted fill: fill's grid is 0.3 samples late; its first retained
    #    sample anchors at ITS stated time (0.503), never shifted onto the
    #    winner's grid.
    out = resolve_segments([seg(0.0, 50, 0, "/win", 1),
                            seg(0.003, 100, 1, "/fill", 2)])
    check("fill keeps its stated grid", out, [(0.0, 50, 1), (0.503, 50, 2)])

    # 5. Same-priority duplicates (both selected winners): deterministic
    #    by (t0, path); second copy fills nothing when fully covered.
    out = resolve_segments([seg(0.0, 100, 0, "/a", 1),
                            seg(0.0, 100, 0, "/b", 2)])
    check("same-priority duplicate dedups", out, [(0.0, 100, 1)])

    # 6. Identical path twice (accidental double routing) collapses.
    out = resolve_segments([seg(0.0, 100, 0, "/a", 1),
                            seg(0.0, 100, 0, "/a", 1),
                            seg(1.0, 100, 0, "/c", 3)])
    check("identical path collapses", out, [(0.0, 100, 1), (1.0, 100, 3)])

    # 7. Gap between winners stays a gap unless a fill covers it.
    out = resolve_segments([seg(0.0, 50, 0, "/a", 1),
                            seg(1.0, 50, 0, "/b", 1),
                            seg(0.0, 150, 2, "/f", 9)])
    check("mid gap filled, edges intact", out,
          [(0.0, 50, 1), (0.5, 50, 9), (1.0, 50, 1)])

    # 8. Mixed rates refuse.
    try:
        resolve_segments([seg(0.0, 10, 0, "/a", 1),
                          dict(seg(0.0, 10, 0, "/b", 1), rate=200.0)])
        print("FAIL  mixed rates refuse")
        FAILURES.append("mixed rates refuse")
    except ValueError:
        print("PASS  mixed rates refuse")

    if FAILURES:
        print(f"\n{len(FAILURES)} resolver test(s) FAILED: {FAILURES}")
        return 1
    print("\nall resolver tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""test_topology — the lineage-aware weld detector (Codex fix review
2026-10-27 F1). The 0.51-sample departures must find a weld; the
0.49-sample ones must not; an unselected aligned alternative must not
mask a break in the selected lineage. Needs numpy (qa_verify import).
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa_verify import topology_breaks  # noqa: E402

RATE = 100.0
FAILURES = []


def tr(start_s, n, path):
    return {"start": types.SimpleNamespace(timestamp=start_s),
            "rate": RATE, "data": [0] * n, "src_path": path, "orient": "Z"}


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name} (breaks={got}, want {want})")
    if not ok:
        FAILURES.append(name)


def main():
    lin = {"/a": "pop1", "/b": "pop1", "/alt": "pop2"}

    # Welded 199-sample output; second same-lineage file 0.51 samples EARLY
    # (Codex's constant-data counterexample: starts 0.9949 s).
    src = [tr(0.0, 100, "/a"), tr(0.9949, 100, "/b")]
    check("0.51-sample early same-lineage weld found",
          topology_breaks(src, [tr(0.0, 199, None)], lin), 1)

    # 0.49 samples early (starts 0.9951 s): within the half-sample bound —
    # the merge appends contiguously, one 200-sample segment, no finding.
    src = [tr(0.0, 100, "/a"), tr(0.9951, 100, "/b")]
    check("0.49-sample early is clean",
          topology_breaks(src, [tr(0.0, 200, None)], lin), 0)

    # 0.51 samples LATE: converter must split; a single trace spanning it
    # is a weld.
    src = [tr(0.0, 100, "/a"), tr(1.0051, 100, "/b")]
    check("0.51-sample late same-lineage weld found",
          topology_breaks(src, [tr(0.0, 200, None)], lin), 1)

    # 0.49 samples late: clean continuation.
    src = [tr(0.0, 100, "/a"), tr(1.0049, 100, "/b")]
    check("0.49-sample late is clean",
          topology_breaks(src, [tr(0.0, 200, None)], lin), 0)

    # Masking: the same 0.51-late selected-lineage weld plus an UNSELECTED
    # perfectly-aligned alternative copy must STILL be found.
    src = [tr(0.0, 100, "/a"), tr(1.0051, 100, "/b"), tr(1.0, 100, "/alt-x")]
    check("unselected alternative cannot mask the break",
          topology_breaks(src, [tr(0.0, 200, None)], lin), 1)

    # The same alternative, SELECTED (different lineage), legitimately
    # bridges the junction — the fill mechanism.
    src = [tr(0.0, 100, "/a"), tr(1.0051, 100, "/b"), tr(1.0, 100, "/alt")]
    check("selected cross-lineage fill is clean",
          topology_breaks(src, [tr(0.0, 200, None)], lin), 0)

    # Clean two-file contiguous lineage.
    src = [tr(0.0, 100, "/a"), tr(1.0, 100, "/b")]
    check("contiguous lineage is clean",
          topology_breaks(src, [tr(0.0, 200, None)], lin), 0)

    # Same-population DUPLICATE whole-minute copy (glitchy telemetry
    # dispatch, both selected): stated starts nearly coincide — allowed.
    lin2 = {"/a": "pop1", "/a2": "pop1", "/b": "pop1"}
    src = [tr(0.0, 100, "/a"), tr(0.02, 100, "/a2"), tr(1.0, 100, "/b")]
    check("same-population whole-minute duplicate is clean",
          topology_breaks(src, [tr(0.0, 200, None)], lin2), 0)

    if FAILURES:
        print(f"\n{len(FAILURES)} topology test(s) FAILED: {FAILURES}")
        return 1
    print("\nall topology tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

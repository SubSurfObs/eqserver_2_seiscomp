#!/usr/bin/env python3
"""test_convert_gates — proves the F1 structural gates refuse (review
2026-10-23). No archive access needed; phase3 must refuse BEFORE touching
the manifest DB, the engine, or any mount.

Checks:
  1. phase3 with neither --selection nor --legacy-chooser → exit 2, refusal.
  2. phase3 with both flags → exit 2, mutually exclusive.
  3. phase3 --commit with a needs_review plan → exit 2, plan gate.
  4. phase3 --commit with a BLOCKED plan → exit 2, plan gate.
  5. run_production_convert without --selections-dir → argparse exit 2.

Run: python3 scan/test_convert_gates.py   (pyyaml required; nothing else)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
FAILURES = []


def check(name, cmd, want_rc, want_text):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    ok = p.returncode == want_rc and want_text in out
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILURES.append(name)
        print(f"      rc={p.returncode} (want {want_rc}); output tail:")
        print("      " + "\n      ".join(out.strip().splitlines()[-5:]))


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = td / "fake.db"
        db.write_bytes(b"")
        plans = {}
        for status in ("needs_review", "BLOCKED"):
            p = td / f"plan_{status}.yaml"
            p.write_text(
                f"station: FAKE\nnetwork: VW\nlocation: '00'\n"
                f"status: {status}\nepochs: []\n")
            plans[status] = p
        base = [PY, str(HERE / "phase3_driver.py"), str(db),
                str(plans["needs_review"]), "--registry", str(td / "reg.yaml"),
                "--staging-sds", str(td / "sds")]

        check("no selection and no legacy flag refuses",
              base, 2, "REFUSING TO RUN: no --selection")
        check("selection + legacy flag refuses",
              base + ["--selection", str(td / "s.sqlite"), "--legacy-chooser"],
              2, "mutually exclusive")
        check("legacy flag + commit refuses (fix-review F1)",
              base + ["--legacy-chooser", "--commit"], 2,
              "cannot be combined with --commit")
        check("commit with needs_review plan refuses",
              base + ["--selection", str(td / "s.sqlite"), "--commit"],
              2, "PLAN GATE REFUSED")
        check("commit with BLOCKED plan refuses",
              [PY, str(HERE / "phase3_driver.py"), str(db),
               str(plans["BLOCKED"]), "--registry", str(td / "reg.yaml"),
               "--staging-sds", str(td / "sds"),
               "--selection", str(td / "s.sqlite"),
               "--commit"], 2, "PLAN GATE REFUSED")
        check("wrapper requires --selections-dir",
              [PY, str(HERE / "run_production_convert.py"),
               "--registry", str(td / "reg.yaml"),
               "--staging-sds", str(td / "sds")], 2, "--selections-dir")

    if FAILURES:
        print(f"\n{len(FAILURES)} gate test(s) FAILED: {FAILURES}")
        return 1
    print("\nall convert-gate tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

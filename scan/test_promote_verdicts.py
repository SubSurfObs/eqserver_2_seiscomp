#!/usr/bin/env python3
"""Promoter gates for the VW/VX re-sweep (Codex 2026-11-26 F3-F5):
two-way inventory with sizes, and a comparison file that covers exactly the
manifest's files, once each, made by the pinned comparator."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_production_promote import inventory_problems, verdict_file_problems  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAIL.append(name)
        if detail:
            print(f"      {detail}")


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        root = td / "st"
        d = root / "2019" / "VW" / "X" / "HHZ.D"
        d.mkdir(parents=True)
        files = []
        for doy in (160, 161):
            f = d / f"VW.X.00.HHZ.D.2019.{doy}"
            f.write_bytes(b"x" * (100 + doy))
            files.append(f)
        man = td / "m.json"

        def manifest(items):
            man.write_text(json.dumps({"eqserver": {"per_date_status": [{"sds_files_written": items}]}}))

        entry = {"run_manifest_path": str(man), "staging_root": str(root), "year": 2019, "net": "VW", "sta": "X"}
        manifest([[str(f), f.stat().st_size] for f in files])
        check("exact inventory", inventory_problems(entry) == [])
        extra = d / "VW.X.00.HHZ.D.2019.162"; extra.write_bytes(b"y")
        check("extra staged file", any("not named" in p for p in inventory_problems(entry)))
        extra.unlink()
        files[1].unlink()
        check("one named file missing", any("missing" in p for p in inventory_problems(entry)))
        files[1].write_bytes(b"x" * 5)
        check("size differs from the record", any("size" in p for p in inventory_problems(entry)))
        entry2 = dict(entry, sta="NOPE")
        manifest([[str(root / "2019/VW/NOPE/HHZ.D/VW.NOPE.00.HHZ.D.2019.160"), 10]])
        check("whole unit directory missing", any("missing" in p for p in inventory_problems(entry2)))
        manifest([])
        check("manifest names nothing", inventory_problems(entry) != [])
        # comparison-file coverage and comparator pin
        manifest([[str(f), 1] for f in files])
        vf = td / "v.jsonl"
        sha = "c" * 64
        vf.write_text("".join(json.dumps({"staged": str(f), "comparator_sha256": sha}) + "\n" for f in files))
        check("verdicts cover the manifest exactly", verdict_file_problems(vf, entry, sha) == [])
        vf.write_text(json.dumps({"staged": str(files[0]), "comparator_sha256": sha}) + "\n")
        check("a manifest file without a verdict", any("no verdict" in p for p in verdict_file_problems(vf, entry, sha)))
        vf.write_text("".join(json.dumps({"staged": str(f), "comparator_sha256": "d" * 64}) + "\n" for f in files))
        check("wrong comparator", any("comparator" in p for p in verdict_file_problems(vf, entry, sha)))
        r = json.dumps({"staged": str(files[0]), "comparator_sha256": sha})
        vf.write_text(r + "\n" + r + "\n" + json.dumps({"staged": str(files[1]), "comparator_sha256": sha}) + "\n")
        check("duplicate verdict path", any("duplicate" in p for p in verdict_file_problems(vf, entry, sha)))
    print("all promoter verdict-gate tests passed" if not FAIL else f"{len(FAIL)} FAILED")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

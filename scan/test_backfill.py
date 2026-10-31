#!/usr/bin/env python3
"""test_backfill — backfill_run_manifests is all-or-nothing (Codex 0ff001e).
Needs phase3_driver importable (run on the VM)."""
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAIL = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAIL.append(name)
        if detail:
            print(f"      {detail}")


def run(q, arch, *extra):
    return subprocess.run([sys.executable, str(HERE / "backfill_run_manifests.py"),
                           "--queues", str(q), "--selection-archive", str(arch),
                           *extra], capture_output=True, text=True)


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        q, arch = td / "q", td / "arch"
        q.mkdir()
        pre = {"repos": [{"repo": "eqserver_2_seiscomp", "head": "a" * 40},
                         {"repo": "disk_to_sds", "head": "b" * 40},
                         {"repo": "sudspy", "head": "c" * 40}]}
        evs = []
        for y in (2024, 2025):
            mp = td / f"m{y}.json"
            mp.write_text(json.dumps({"run_id": f"phase3_{y}",
                                      "selection": {"path": f"/tmp/x{y}"}}))
            evs.append({"run_id": f"eqserver_DU_FAKE_{y}_T", "net": "DU",
                        "sta": "FAKE", "year": y, "run_manifest_path": str(mp),
                        "preflight": pre})
        (q / "convert_done.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in evs))
        d = arch / "DU.FAKE"
        d.mkdir(parents=True)
        with gzip.open(d / "DU.FAKE.2024.selection.sqlite.gz", "wb") as f:
            f.write(b"sel2024")
        before = {e["year"]: Path(e["run_manifest_path"]).read_text() for e in evs}

        r = run(q, arch, "--commit")       # 2025 has no archived selection
        after = {e["year"]: Path(e["run_manifest_path"]).read_text() for e in evs}
        check("any incomplete record -> nonzero exit", r.returncode != 0, r.stdout)
        check("...and NOTHING written (complete one untouched too)",
              after == before)

        with gzip.open(d / "DU.FAKE.2025.selection.sqlite.gz", "wb") as f:
            f.write(b"sel2025")
        r = run(q, arch, "--commit")
        check("all complete -> exit 0", r.returncode == 0, r.stdout + r.stderr)
        m = json.loads(Path(evs[0]["run_manifest_path"]).read_text())
        check("run_id is the queue's; old kept",
              m["run_id"] == "eqserver_DU_FAKE_2024_T"
              and m["phase3_run_id"] == "phase3_2024")
        check("selection path is the permanent archive",
              m["selection"]["path"].endswith("DU.FAKE.2024.selection.sqlite.gz")
              and m["selection"]["path_at_conversion"] == "/tmp/x2024")
        check("historical python marked unknown, backfill stack separate",
              m["python"]["historical"].startswith("unknown")
              and "observed_at_backfill" in m["python"])
        check("release SHAs recorded", m["release"]["sudspy"] == "c" * 40)
    if FAIL:
        print(f"\n{len(FAIL)} FAILED")
        return 1
    print("\nall backfill tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

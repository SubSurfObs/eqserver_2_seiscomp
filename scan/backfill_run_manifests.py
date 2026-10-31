#!/usr/bin/env python3
"""backfill_run_manifests — metadata-only repair before promotion (Codex
c835400 F1). For every convert_done event under the given queue dirs, make
its run manifest carry ONE identity and the full release record:

  - run_id      := the queue event's run_id (old value kept as phase3_run_id)
  - release     := eqserver / disk_to_sds / sudspy HEADs from the event's
                   preflight record
  - selection   := archived selection file (path + sha256 of its content)
  - python      := historical environment marked unknown; the backfill-
                   time stack is recorded separately as observed_at_backfill

Validates EVERY record first: any missing manifest, release SHA or archived
selection exits nonzero and writes nothing.

Waveform files are never touched. Dry-run by default; --commit writes each
manifest atomically and keeps the original as <name>.pre_backfill.

Usage: backfill_run_manifests.py --queues /mnt/.../eqserver_sweep_du_v1_s* \
         --selection-archive /mnt/.../eqserver_v3_selections/du_v1 [--commit]
"""
import argparse
import glob
import gzip
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase3_driver import _python_stack  # noqa: E402


def selection_record(archive, net, sta, year):
    gz = Path(archive) / f"{net}.{sta}" / f"{net}.{sta}.{year}.selection.sqlite.gz"
    if not gz.exists():
        return None
    h = hashlib.sha256()
    with gzip.open(gz, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": str(gz), "sha256": h.hexdigest(),
            "sha256_of": "decompressed selection database"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queues", nargs="+", required=True)
    ap.add_argument("--selection-archive", required=True)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    # Phase 1: build EVERY repaired manifest in memory; any gap aborts the
    # whole run with nothing written (Codex 0ff001e).
    plans, problems = [], []
    for q in a.queues:
        done = Path(q) / "convert_done.jsonl"
        if not done.exists():
            continue
        for line in done.open():
            ev = json.loads(line)
            mp = Path(ev.get("run_manifest_path") or "")
            if not mp.is_file():
                problems.append(f"{ev.get('run_id')}: manifest missing ({mp})")
                continue
            m = json.loads(mp.read_text())
            heads = {r["repo"]: r["head"]
                     for r in (ev.get("preflight") or {}).get("repos", [])}
            release = {"eqserver": heads.get("eqserver_2_seiscomp"),
                       "disk_to_sds": heads.get("disk_to_sds"),
                       "sudspy": heads.get("sudspy")}
            sel = selection_record(a.selection_archive, ev["net"], ev["sta"],
                                   ev["year"])
            gaps = [k for k, v in release.items() if not v]
            if not sel:
                gaps.append("archived selection")
            if gaps:
                problems.append(f"{ev['run_id']}: missing {gaps}")
                continue
            if m.get("run_id") != ev["run_id"]:
                m.setdefault("phase3_run_id", m.get("run_id"))
                m["run_id"] = ev["run_id"]
            m["release"] = release
            # Permanent location only; the conversion-time local path is kept
            # for reference (it no longer exists).
            old_sel = m.get("selection") or {}
            sel["path_at_conversion"] = (old_sel.get("path")
                                         or m.get("selection_path"))
            m["selection"] = sel
            # The conversion-time environment was not recorded for these runs.
            if not (m.get("python") or {}).get("python"):
                m["python"] = {"historical": "unknown — not recorded at "
                                             "conversion",
                               "observed_at_backfill": _python_stack()}
            plans.append((ev["run_id"], mp, m))
    for p in problems:
        print(f"INCOMPLETE  {p}")
    print(f"{len(plans)} complete, {len(problems)} incomplete")
    if problems:
        print("ABORT: nothing written — fix the gaps above and rerun.")
        sys.exit(1)
    if not a.commit:
        print("(dry run — nothing written)")
        return
    # Phase 2: write, each atomically, original kept once.
    for run_id, mp, m in plans:
        bak = mp.with_name(mp.name + ".pre_backfill")
        if not bak.exists():
            shutil.copy2(mp, bak)
        tmp = mp.with_name(mp.name + ".partial")
        tmp.write_text(json.dumps(m, indent=2))
        os.replace(tmp, mp)
    print(f"wrote {len(plans)} manifests")


if __name__ == "__main__":
    main()

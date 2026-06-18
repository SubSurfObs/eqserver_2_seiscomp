#!/usr/bin/env python3
"""side_load_window_audit.py — list station-years whose convert ran
during the engine side-load window (Issue 12 Class B blast radius).

Background: the disk_to_sds team verified 2026-06-18 that the current
committed engine handles Gecko per-minute merging correctly. The
fragmented production LT files are stale-engine artifacts written
during a window when the VM had a side-loaded (non-git-tracked) copy
of suds_convert.py.

Window endpoints:
- Start: 2026-06-01 (first git-tracked engine commit 9a3b2ae)
- End:   2026-06-12 (git-synced-across-hosts rule tightened, current
                     pin 94ff229 landed)

For every entry in convert_done.jsonl, this script:
1. Parses the convert timestamp
2. Checks if it falls in the window
3. Cross-references with station_registry.yaml for recorder_types
4. Reports the candidate Gecko/Minimus units for re-conversion

USAGE:
  python3 scan/side_load_window_audit.py \\
      --queue-dir /mnt/seiscomp_staging/eqserver_sweep \\
      --registry metadata/station_registry.yaml \\
      --window-start 2026-06-01 \\
      --window-end 2026-06-12 \\
      --out /tmp/side_load_blast_radius.txt
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml


# Recorder types that exhibit the Class B fragmentation pattern under a
# stale engine (per-minute file architecture that needs the merge step).
AFFECTED_RECORDERS = {"gecko", "minimus", "reftek_rt130"}


def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_registry_recorders(registry_path: Path) -> dict[str, list[str]]:
    """Return {station: [recorder_types]}."""
    reg = yaml.safe_load(open(registry_path))
    out = {}
    for sta, e in reg.items():
        if not isinstance(e, dict):
            continue
        types = e.get("recorder_types") or []
        if isinstance(types, list):
            out[sta] = [t.lower() for t in types if isinstance(t, str)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue-dir", type=Path, required=True)
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--window-start", default="2026-06-01",
                    help="ISO date (inclusive)")
    ap.add_argument("--window-end", default="2026-06-12",
                    help="ISO date (exclusive — events at/after this are post-fix)")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    window_start = datetime.fromisoformat(args.window_start + "T00:00:00+00:00")
    window_end = datetime.fromisoformat(args.window_end + "T00:00:00+00:00")

    convert_done = args.queue_dir / "convert_done.jsonl"
    if not convert_done.exists():
        print(f"ERROR: {convert_done} not found", file=sys.stderr)
        return 2

    registry_recorders = load_registry_recorders(args.registry)

    in_window: list[dict] = []
    in_window_unknown_recorder: list[dict] = []
    out_of_window: int = 0
    no_ts: int = 0

    for line in convert_done.open():
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = parse_ts(d.get("ts"))
        if not ts:
            no_ts += 1
            continue
        if not (window_start <= ts < window_end):
            out_of_window += 1
            continue
        sta = d.get("sta")
        year = d.get("year")
        net = d.get("net")
        recorders = registry_recorders.get(sta) or []
        in_affected = any(r in AFFECTED_RECORDERS for r in recorders)
        row = {
            "net": net, "sta": sta, "year": year,
            "ts": d.get("ts"), "run_id": d.get("run_id"),
            "recorder_types": recorders,
            "items_succeeded": d.get("items_succeeded"),
            "in_affected_recorder_set": in_affected,
        }
        if recorders == []:
            in_window_unknown_recorder.append(row)
        elif in_affected:
            in_window.append(row)
        # If recorder is known but not in affected set (echopro only), drop.

    fh = sys.stdout if args.out == "-" else open(args.out, "w")

    fh.write(f"# Side-load-window blast radius audit\n")
    fh.write(f"# Window: [{args.window_start}, {args.window_end}) UTC\n")
    fh.write(f"# convert_done.jsonl: total entries parsed (excl. no-ts: {no_ts})\n")
    fh.write(f"#   out of window: {out_of_window}\n")
    fh.write(f"#   in window, affected recorder (Gecko/Minimus/Reftek): {len(in_window)}\n")
    fh.write(f"#   in window, registry recorder_types missing (need check): {len(in_window_unknown_recorder)}\n")
    fh.write("#\n")
    fh.write("# Per-station count and year list (affected recorders in window):\n")

    by_sta = defaultdict(list)
    for r in in_window:
        by_sta[r["sta"]].append(r["year"])
    for sta in sorted(by_sta):
        years = sorted(set(by_sta[sta]))
        recs = registry_recorders.get(sta, [])
        fh.write(f"  {sta:8s}  recorders={recs}  years={years}\n")

    fh.write("\n# Per-station count (registry recorder_types EMPTY — manual review):\n")
    by_sta_unknown = defaultdict(list)
    for r in in_window_unknown_recorder:
        by_sta_unknown[r["sta"]].append(r["year"])
    for sta in sorted(by_sta_unknown):
        years = sorted(set(by_sta_unknown[sta]))
        fh.write(f"  {sta:8s}  recorders=[]  years={years}\n")

    fh.write("\n# Total candidates for re-conversion: ")
    fh.write(f"{len(in_window)} affected + {len(in_window_unknown_recorder)} unknown = "
             f"{len(in_window) + len(in_window_unknown_recorder)} station-years\n")

    if args.out != "-":
        fh.close()
        print(f"[audit] {len(in_window)} affected, "
              f"{len(in_window_unknown_recorder)} unknown — "
              f"results: {args.out}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

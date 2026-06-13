#!/usr/bin/env python3
"""backfill_resolved_events.py — one-time migration.

Reconciles convert_failed.jsonl with the recovery work that happened BEFORE
the action="resolved" event type existed. Walks promote_done.jsonl for
recovery runs (run_id contains '_recovery_' or '_recovered_') and, for each
(net, sta, year) that ALSO has a no-action entry in convert_failed.jsonl,
appends a matching resolved event linking the original failed run_id to
the recovery run_id.

Idempotent — checks for existing resolved events with the same
(original_run_id, recovery_run_id) pair before appending.

DRY-RUN BY DEFAULT. Pass --commit to actually append.

Run on the staging VM (same host as convert.py and recovery script — keeps
the single-writer-per-file invariant intact).

  python3 scan/backfill_resolved_events.py \\
      --queue-dir /mnt/seiscomp_staging/eqserver_sweep
  # then with --commit when the dry-run looks right.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue-dir", type=Path, required=True)
    ap.add_argument("--commit", action="store_true",
                    help="actually append events (default: dry-run)")
    args = ap.parse_args()

    queue = args.queue_dir
    convert_failed_path = queue / "convert_failed.jsonl"
    promote_done_path = queue / "promote_done.jsonl"

    if not convert_failed_path.exists():
        print(f"ERROR: convert_failed.jsonl not at {convert_failed_path}",
              file=sys.stderr)
        return 1
    if not promote_done_path.exists():
        print(f"ERROR: promote_done.jsonl not at {promote_done_path}",
              file=sys.stderr)
        return 1

    failed_events = read_jsonl(convert_failed_path)
    promote_events = read_jsonl(promote_done_path)

    # Build the set of already-resolved (original_run_id, recovery_run_id) pairs
    # so we don't double-backfill.
    already_resolved: set[tuple[str | None, str]] = set()
    for e in failed_events:
        if e.get("action") == "resolved":
            already_resolved.add(
                (e.get("original_run_id"), e.get("recovery_run_id"))
            )

    # Per-unit: failed events (action != resolved) keyed by run_id
    # plus a sorted list of recovery promote events.
    failed_by_unit: dict[tuple, list[dict]] = {}
    for e in failed_events:
        if e.get("action") == "resolved":
            continue
        key = (e.get("net"), e.get("sta"), e.get("year"))
        failed_by_unit.setdefault(key, []).append(e)

    # Identify resolution promotes — any promote_done entry with
    # action="promoted" for a (net, sta, year) that ALSO has a failed
    # event. Whether the resolution came through the dedicated recovery
    # script (run_recovery_register.py, run_ids marked _recovery_ /
    # _recovered_) OR through a plain orchestrator re-run with a normal
    # run_id is semantically equivalent: the failure is no longer
    # outstanding because the unit reached promoted. The unit-table
    # state machine treats them the same.
    recovery_promotes: list[dict] = []
    for e in promote_events:
        if e.get("action") != "promoted":
            continue
        key = (e.get("net"), e.get("sta"), e.get("year"))
        if key in failed_by_unit:
            recovery_promotes.append(e)

    print(f"convert_failed.jsonl: {len(failed_events)} total events "
          f"({sum(1 for e in failed_events if e.get('action')=='resolved')} "
          f"already resolved, {len(failed_by_unit)} distinct failed units)")
    print(f"promote_done.jsonl: {len(promote_events)} total "
          f"({len(recovery_promotes)} are recovery promotes)")
    print()

    # Backfill plan: for each recovery promote, find a matching failed
    # event for the same (net, sta, year) that has no resolved companion
    # yet. Pick the LATEST such failed event (by ts).
    to_append: list[dict] = []
    for rp in recovery_promotes:
        key = (rp.get("net"), rp.get("sta"), rp.get("year"))
        recovery_rid = rp.get("run_id")
        candidates = failed_by_unit.get(key, [])
        if not candidates:
            # No failed event recorded but a recovery happened. Append a
            # resolved event with original_run_id=None — semantically
            # "the unit was recovered without our having a recorded
            # failure to link to."
            if (None, recovery_rid) in already_resolved:
                continue
            to_append.append({
                "action": "resolved",
                "net": key[0], "sta": key[1], "year": key[2],
                "original_run_id": None,
                "recovery_run_id": recovery_rid,
                "ts": utc_now_iso(),
                "backfilled": True,
                "backfill_note": "no recorded failure; recovery promote linked retrospectively",
            })
            continue
        # Find the latest unresolved failed event for this unit
        # (in case multiple failures happened before recovery).
        unresolved = []
        for cand in candidates:
            cand_rid = cand.get("run_id")
            if (cand_rid, recovery_rid) in already_resolved:
                continue
            # Also skip if ANY resolved event already linked this
            # original_run_id (it's already accounted for).
            if any(orid == cand_rid for orid, _ in already_resolved):
                continue
            unresolved.append(cand)
        if not unresolved:
            continue
        unresolved.sort(key=lambda e: e.get("ts", ""))
        chosen = unresolved[-1]
        to_append.append({
            "action": "resolved",
            "net": key[0], "sta": key[1], "year": key[2],
            "original_run_id": chosen.get("run_id"),
            "original_ts": chosen.get("ts"),
            "recovery_run_id": recovery_rid,
            "recovery_ts": rp.get("ts"),
            "ts": utc_now_iso(),
            "backfilled": True,
        })

    print(f"Backfill plan: {len(to_append)} resolved event(s) to append")
    print()
    for ev in to_append:
        print(f"  {ev['net']}.{ev['sta']}.{ev['year']}  "
              f"failed_run_id={ev.get('original_run_id', 'NONE')}  "
              f"recovery_run_id={ev['recovery_run_id']}")
    print()

    if not to_append:
        print("Nothing to backfill.")
        return 0

    if not args.commit:
        print("DRY-RUN — re-run with --commit to actually append.")
        return 0

    # Append all events. Same-host write — single writer per file.
    with convert_failed_path.open("a") as f:
        for ev in to_append:
            line = json.dumps(ev, separators=(",", ":"), sort_keys=True)
            f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(f"Appended {len(to_append)} resolved event(s) to {convert_failed_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

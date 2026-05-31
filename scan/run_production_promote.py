#!/usr/bin/env python3
"""run_production_promote.py — watches pending.jsonl on the shared staging
mount, invokes sds_staging_ledger/apply.py for each new entry, appends to
promoted.jsonl on success.

Runs on **dev1** (the SeisComP VM that owns /mnt/seiscomp_archive rw and
has the staging mount visible). One half of the cross-host orchestration.
The convert.py half runs on the staging VM; the cleanup.py half also runs
on the staging VM.

Polling-watcher pattern: keeps a byte offset into pending.jsonl, polls on
an interval, reads new entries since last position, invokes apply.py per
entry. Within ~poll-interval seconds of phase3 finishing a (station, year),
the bytes start moving into LT.

Resume: at startup, reads promoted.jsonl and builds the set of run_ids
already promoted. Re-poll the queue file from offset 0 and skip
already-promoted entries.

Run (as a long-lived watcher):
  python3 scan/run_production_promote.py \
      --staging-root /mnt/seiscomp_staging/production \
      --lt-root /mnt/seiscomp_archive \
      --ledger-root /home/.../sds_staging_ledger/seiscomp_archive \
      --poll-interval 30
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from orchestrate_queue import (
    queue_dir, append_event, read_all_events, already_processed_ids, utc_now,
)

DEFAULT_APPLY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/sds_staging_ledger/apply.py"
DEFAULT_VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"


def invoke_apply(args, entry: dict) -> dict:
    """Invoke apply.py for one pending entry. Returns result dict."""
    log_path = Path(args.log_dir) / f"{entry['run_id']}.apply.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [args.python, "-u", args.apply,
           "--staging-root", entry["staging_root"],
           "--lt-root", args.lt_root,
           "--ledger-root", args.ledger_root,
           "--net", entry["net"],
           "--sta", entry["sta"],
           "--source-kind", "eqserver",
           "--run-manifest", entry["run_manifest_path"],
           "--mode", args.mode,
           "--commit"]
    if args.no_autocommit:
        cmd.append("--no-autocommit")
    t0 = time.time()
    with log_path.open("w") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, text=True)
    elapsed = time.time() - t0
    return {
        "status": "ok" if proc.returncode == 0 else "fail",
        "rc": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "log_path": str(log_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging-root", required=True,
                    help="SDS root the producer wrote to, "
                         "e.g. /mnt/seiscomp_staging/production. Used to locate "
                         "the queue dir (defaults to <parent>/eqserver_queue).")
    ap.add_argument("--lt-root", required=True,
                    help="long-term SDS root, e.g. /mnt/seiscomp_archive")
    ap.add_argument("--ledger-root", required=True,
                    help="ledger manifest tree root "
                         "(the seiscomp_archive/ subdir of the ledger repo)")
    ap.add_argument("--queue-dir", default=None,
                    help="default: <staging-root-parent>/eqserver_queue")
    ap.add_argument("--mode", choices=["decide", "overwrite"], default="decide",
                    help="apply.py decision mode (default decide = gap-fill)")
    ap.add_argument("--apply", default=DEFAULT_APPLY)
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    ap.add_argument("--log-dir", default="/tmp/eqserver_promote_logs")
    ap.add_argument("--poll-interval", type=int, default=30,
                    help="seconds between polls of pending.jsonl (default 30)")
    ap.add_argument("--once", action="store_true",
                    help="process current pending entries then exit "
                         "(default: poll forever)")
    ap.add_argument("--no-autocommit", action="store_true",
                    help="pass --no-autocommit to apply.py (skip the ledger "
                         "git commit+push after each apply)")
    ap.add_argument("--skip-empty", action="store_true",
                    help="skip entries with items_succeeded == 0 "
                         "(no SDS to promote)")
    args = ap.parse_args()

    queue = Path(args.queue_dir) if args.queue_dir else \
            queue_dir(Path(args.staging_root).parent)
    pending = queue / "pending.jsonl"
    promoted = queue / "promoted.jsonl"
    queue.mkdir(parents=True, exist_ok=True)

    # Resume: which run_ids already promoted?
    done = already_processed_ids(promoted)
    print(f"[promote] queue dir: {queue}", flush=True)
    print(f"[promote] already promoted: {len(done)} entries", flush=True)
    print(f"[promote] mode={args.mode} poll={args.poll_interval}s "
          f"once={args.once}", flush=True)

    iteration = 0
    while True:
        iteration += 1
        new_entries = []
        for entry in read_all_events(pending):
            if entry.get("run_id") in done:
                continue
            new_entries.append(entry)

        if new_entries:
            print(f"[promote] iteration {iteration}: {len(new_entries)} new entries",
                  flush=True)
            for entry in new_entries:
                rid = entry["run_id"]
                if args.skip_empty and entry.get("items_succeeded", 0) == 0:
                    print(f"[promote]   skip {rid}: items_succeeded=0", flush=True)
                    # Still record so we don't re-evaluate every poll
                    skip_event = {
                        "run_id": rid, "net": entry["net"], "sta": entry["sta"],
                        "year": entry["year"], "action": "skip-empty",
                        "ts": utc_now(),
                    }
                    append_event(promoted, skip_event)
                    done.add(rid)
                    continue
                print(f"[promote]   apply {rid} (sta={entry['sta']} "
                      f"year={entry['year']}) ...", flush=True)
                r = invoke_apply(args, entry)
                if r["status"] == "ok":
                    out_event = {
                        "run_id": rid, "net": entry["net"], "sta": entry["sta"],
                        "year": entry["year"], "action": "promoted",
                        "elapsed_s": r["elapsed_s"], "ts": utc_now(),
                    }
                    append_event(promoted, out_event)
                    done.add(rid)
                    print(f"[promote]   OK {rid} {r['elapsed_s']:.0f}s", flush=True)
                else:
                    print(f"[promote]   FAIL {rid} rc={r['rc']} "
                          f"log={r['log_path']}", flush=True)
                    # Don't add to `done`; will retry on next poll. Persistent
                    # failures will hammer the log; operator should intervene.

        if args.once:
            break
        try:
            time.sleep(args.poll_interval)
        except KeyboardInterrupt:
            print("\n[promote] interrupted; exiting cleanly", flush=True)
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())

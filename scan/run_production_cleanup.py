#!/usr/bin/env python3
"""run_production_cleanup.py — watches promote_done.jsonl, invokes
sds_staging_ledger/cleanup.py per entry to free the staged copy.

Runs on the **staging VM** (has staging rw + LT ro mounted; the cleanup.py
tool requires both). Third script in the cross-host orchestration trio.

The ledger's cleanup.py confirms LT == staging (size or checksum compare)
and deletes the staged copy. This wrapper invokes it per (sta, year) entry
that promote.py reported as successfully promoted, with the right scoping
flags so a wide staging tree doesn't accidentally get swept.

Note this script's filename DOES NOT shadow the ledger's cleanup.py — the
ledger script is at `sds_staging_ledger/cleanup.py`, this one is
`scan/run_production_cleanup.py`. They never collide.

**Updated 2026-05-31 per disk_to_sds reply 03.** Reads promote_done.jsonl
(written by dev1) from the shared queue dir, scopes cleanup.py to (--net,
--sta), and writes to cleanup_done.jsonl. Single-writer on each file; no
SSH between hosts.

Run (as a long-lived watcher):
  python3 scan/run_production_cleanup.py \
      --staging-root /mnt/seiscomp_staging/seiscomp_archive \
      --lt-root /mnt/seiscomp_archive \
      --ledger-root /home/.../sds_staging_ledger/seiscomp_archive \
      --poll-interval 60
"""
from __future__ import annotations
import argparse
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

DEFAULT_CLEANUP = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/sds_staging_ledger/cleanup.py"
DEFAULT_VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"


def invoke_cleanup(args, entry: dict) -> dict:
    """Invoke ledger's cleanup.py for one promoted entry."""
    log_path = Path(args.log_dir) / f"{entry['run_id']}.cleanup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [args.python, "-u", args.cleanup,
           "--staging-root", args.staging_root,
           "--lt-root", args.lt_root,
           "--ledger-root", args.ledger_root,
           "--net", entry["net"],
           "--sta", entry["sta"],
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
    ap.add_argument("--staging-root", required=True)
    ap.add_argument("--lt-root", required=True,
                    help="LT root mounted ro on the staging VM "
                         "(/mnt/seiscomp_archive)")
    ap.add_argument("--ledger-root", required=True)
    ap.add_argument("--queue-dir", default=None,
                    help="default: <staging-root-parent>/eqserver_queue")
    ap.add_argument("--cleanup", default=DEFAULT_CLEANUP)
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    ap.add_argument("--log-dir", default="/tmp/eqserver_cleanup_logs")
    ap.add_argument("--poll-interval", type=int, default=60,
                    help="seconds between polls of promote_done.jsonl (default 60 — "
                         "deliberately slower than promote.py's poll, gives "
                         "apply.py time to finalize)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-autocommit", action="store_true")
    args = ap.parse_args()

    queue = Path(args.queue_dir) if args.queue_dir else \
            queue_dir(Path(args.staging_root).parent)
    promoted = queue / "promote_done.jsonl"
    cleaned = queue / "cleanup_done.jsonl"
    queue.mkdir(parents=True, exist_ok=True)

    done = already_processed_ids(cleaned)
    print(f"[cleanup] queue dir: {queue}", flush=True)
    print(f"[cleanup] already cleaned: {len(done)} entries", flush=True)
    print(f"[cleanup] poll={args.poll_interval}s once={args.once}", flush=True)

    iteration = 0
    while True:
        iteration += 1
        new_entries = []
        for entry in read_all_events(promoted):
            if entry.get("run_id") in done:
                continue
            if entry.get("action") != "promoted":
                # skip-empty entries from promote.py have action="skip-empty"
                # and don't need cleanup (nothing was staged for them).
                # Still record so we don't re-evaluate.
                skip_event = {
                    "run_id": entry["run_id"], "net": entry["net"],
                    "sta": entry["sta"], "year": entry["year"],
                    "action": "skip", "reason": f"upstream action={entry.get('action')}",
                    "ts": utc_now(),
                }
                append_event(cleaned, skip_event)
                done.add(entry["run_id"])
                continue
            new_entries.append(entry)

        if new_entries:
            print(f"[cleanup] iteration {iteration}: {len(new_entries)} new entries",
                  flush=True)
            for entry in new_entries:
                rid = entry["run_id"]
                print(f"[cleanup]   {rid} (sta={entry['sta']} "
                      f"year={entry['year']}) ...", flush=True)
                r = invoke_cleanup(args, entry)
                if r["status"] == "ok":
                    out_event = {
                        "run_id": rid, "net": entry["net"], "sta": entry["sta"],
                        "year": entry["year"], "action": "cleaned",
                        "elapsed_s": r["elapsed_s"], "ts": utc_now(),
                    }
                    append_event(cleaned, out_event)
                    done.add(rid)
                    print(f"[cleanup]   OK {rid} {r['elapsed_s']:.0f}s", flush=True)
                else:
                    print(f"[cleanup]   FAIL {rid} rc={r['rc']} "
                          f"log={r['log_path']}", flush=True)
                    # Don't add to done; retry on next poll. Persistent
                    # failures are operator-investigatable from the logs.

        if args.once:
            break
        try:
            time.sleep(args.poll_interval)
        except KeyboardInterrupt:
            print("\n[cleanup] interrupted; exiting cleanly", flush=True)
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())

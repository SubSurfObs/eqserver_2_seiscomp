#!/usr/bin/env python3
"""run_production_promote.py — runs on dev1, tails convert_done.jsonl on the
shared staging mount, invokes apply.py with the commit-iff-no-overrides gate,
appends to promote_done.jsonl or held.jsonl.

Runs on **dev1** (the SeisComp VM that owns /mnt/seiscomp_archive rw and
mounts the shared staging CIFS share). Per disk_to_sds reply 03 (2026-05-31):
file-queue on shared mount, NO SSH between hosts.

The commit gate (operator-approved policy 2026-05-31):

  1. Invoke apply.py --mode decide WITHOUT --commit (dry-run).
  2. Parse the dry-run's tail summary: "write=N  override=M  skip=K  fail=J".
  3. If M (override count) == 0:
       → invoke apply.py --mode decide --commit
       → append to promote_done.jsonl
     If M > 0:
       → append to held.jsonl (operator review surface)
       → skip --commit

Why this works for eqserver but NOT for disk_to_sds: ~99% of (sta, year)
units have nothing in LT, so apply.py decides "write" everywhere (M=0) and
the auto-commit fires safely. The rare M>0 case is the only genuine
collision and the only place that needs human eyes.

NEVER use --fast — it's size-only and blindly overwrites, destroying the
override detection that gives us the safety gate. (--fast is correct only
when you've ALREADY established the unit is all-write, like the WLSH
card. We can't know that ahead of time for an unattended sweep.)

Resume: at startup, reads promote_done.jsonl AND held.jsonl, builds a set
of run_ids already handled. Polls convert_done.jsonl on interval; for each
new entry, runs the gate. Failed apply.py runs are NOT appended → naturally
retried on next poll.

Run (long-lived, on dev1, as user `seiscomp`):
  python3 scan/run_production_promote.py \\
      --staging-root /mnt/seiscomp_staging/seiscomp_archive \\
      --lt-root /mnt/seiscomp_archive \\
      --ledger-root ~/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive \\
      --poll-interval 60
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from orchestrate_queue import (
    queue_dir, append_event, read_all_events, utc_now,
)

DEFAULT_APPLY = "/home/seiscomp/projects/SubSurfObs/sds_staging_ledger/apply.py"
DEFAULT_PYTHON = "python3"  # dev1 may use system python or a venv; configurable

# Regex for apply.py's summary line:
#   "write=N  override=M  skip=K  fail=J"
# or "would write=N  would override=M  skip=K  fail=J" (dry-run mode)
SUMMARY_RE = re.compile(
    r"(?:would\s+)?write=(\d+)\s+(?:would\s+)?override=(\d+)\s+skip=(\d+)\s+fail=(\d+)"
)


def parse_apply_summary(stdout: str) -> dict | None:
    """Extract counts from apply.py's tail summary line."""
    for line in reversed(stdout.splitlines()):
        m = SUMMARY_RE.search(line)
        if m:
            return {
                "write": int(m.group(1)),
                "override": int(m.group(2)),
                "skip": int(m.group(3)),
                "fail": int(m.group(4)),
            }
    return None


def invoke_apply(args, entry: dict, commit: bool) -> dict:
    """Invoke apply.py for one entry. `commit=False` runs dry-run; `True` adds --commit.
    Returns parsed result dict including counts."""
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_name = f"{entry['run_id']}.{'commit' if commit else 'dry'}.log"
    log_path = log_dir / log_name
    cmd = [args.python, "-u", args.apply,
           "--staging-root", entry["staging_root"],
           "--lt-root", args.lt_root,
           "--ledger-root", args.ledger_root,
           "--net", entry["net"],
           "--sta", entry["sta"],
           "--year", str(entry["year"]),
           "--source-kind", "eqserver",
           "--run-manifest", entry["run_manifest_path"],
           "--mode", "decide"]
    if commit:
        cmd.append("--commit")
    if args.no_autocommit:
        cmd.append("--no-autocommit")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    log_path.write_text(proc.stdout + ("\n=== STDERR ===\n" + proc.stderr if proc.stderr else ""))
    summary = parse_apply_summary(proc.stdout)
    return {
        "rc": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "summary": summary,
        "log_path": str(log_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging-root", required=True,
                    help="Shared staging SDS, e.g. /mnt/seiscomp_staging/seiscomp_archive")
    ap.add_argument("--lt-root", required=True,
                    help="LT root (writable on dev1), e.g. /mnt/seiscomp_archive")
    ap.add_argument("--ledger-root", required=True,
                    help="ledger manifest tree root, e.g. "
                         "~/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive")
    ap.add_argument("--queue-dir", default=None,
                    help="default: <staging-root-parent>/eqserver_sweep")
    ap.add_argument("--apply", default=DEFAULT_APPLY)
    ap.add_argument("--python", default=DEFAULT_PYTHON)
    ap.add_argument("--log-dir", default="/tmp/eqserver_promote_logs")
    ap.add_argument("--poll-interval", type=int, default=60,
                    help="seconds between polls of convert_done.jsonl")
    ap.add_argument("--once", action="store_true",
                    help="process current entries then exit (default: poll forever)")
    ap.add_argument("--no-autocommit", action="store_true",
                    help="pass --no-autocommit to apply.py (skip ledger git push)")
    ap.add_argument("--skip-empty", action="store_true",
                    help="skip convert_done entries with items_succeeded == 0 "
                         "(nothing to promote — record them as handled to avoid "
                         "polling overhead)")
    args = ap.parse_args()

    queue = Path(args.queue_dir) if args.queue_dir else \
            queue_dir(Path(args.staging_root).parent)
    queue.mkdir(parents=True, exist_ok=True)
    convert_done = queue / "convert_done.jsonl"
    promote_done = queue / "promote_done.jsonl"
    held = queue / "held.jsonl"

    # Build resume set from both promote_done and held — either disposition
    # means we're done with that run_id.
    handled: set[str] = set()
    for e in read_all_events(promote_done):
        if "run_id" in e:
            handled.add(e["run_id"])
    for e in read_all_events(held):
        if "run_id" in e:
            handled.add(e["run_id"])

    print(f"[promote] queue dir: {queue}", flush=True)
    print(f"[promote] already handled (promoted + held): {len(handled)}", flush=True)
    print(f"[promote] poll={args.poll_interval}s once={args.once}", flush=True)
    print(f"[promote] apply.py: {args.apply}", flush=True)
    print(f"[promote] LT root: {args.lt_root}", flush=True)

    iteration = 0
    while True:
        iteration += 1
        try:
            entries = read_all_events(convert_done)
        except FileNotFoundError:
            entries = []
        new = [e for e in entries if e.get("run_id") and e["run_id"] not in handled]

        for entry in new:
            rid = entry["run_id"]
            sta = entry["sta"]
            year = entry["year"]

            # Skip-empty short-circuit
            if args.skip_empty and entry.get("items_succeeded", 0) == 0:
                print(f"[promote] {rid} SKIP-EMPTY (items_succeeded=0)", flush=True)
                append_event(promote_done, {
                    "run_id": rid, "net": entry["net"], "sta": sta, "year": year,
                    "action": "skip-empty", "ts": utc_now(),
                })
                handled.add(rid)
                continue

            # Step 1: dry-run
            print(f"[promote] {rid} dry-run ...", flush=True)
            dry = invoke_apply(args, entry, commit=False)
            if dry["rc"] != 0 or dry["summary"] is None:
                print(f"[promote] {rid} DRY-RUN FAILED rc={dry['rc']} "
                      f"summary={dry['summary']} log={dry['log_path']}",
                      flush=True)
                # Don't add to handled — will retry on next poll
                continue
            s = dry["summary"]
            print(f"[promote] {rid} dry-run: write={s['write']} "
                  f"override={s['override']} skip={s['skip']} fail={s['fail']} "
                  f"({dry['elapsed_s']:.0f}s)", flush=True)

            # Step 2: gate on override count
            if s["override"] > 0:
                print(f"[promote] {rid} HELD (overrides={s['override']}) — "
                      "needs human review", flush=True)
                append_event(held, {
                    "run_id": rid, "net": entry["net"], "sta": sta, "year": year,
                    "action": "held", "reason": "overrides > 0",
                    "dry_run_summary": s, "log_path": dry["log_path"],
                    "ts": utc_now(),
                })
                handled.add(rid)
                continue

            # Step 3: zero overrides — auto-commit
            print(f"[promote] {rid} committing (zero overrides) ...", flush=True)
            commit_r = invoke_apply(args, entry, commit=True)
            if commit_r["rc"] != 0:
                print(f"[promote] {rid} COMMIT FAILED rc={commit_r['rc']} "
                      f"log={commit_r['log_path']}", flush=True)
                # Don't add to handled — will retry on next poll
                continue
            cs = commit_r["summary"] or {}
            print(f"[promote] {rid} COMMITTED write={cs.get('write','?')} "
                  f"override={cs.get('override','?')} skip={cs.get('skip','?')} "
                  f"fail={cs.get('fail','?')} ({commit_r['elapsed_s']:.0f}s)",
                  flush=True)
            append_event(promote_done, {
                "run_id": rid, "net": entry["net"], "sta": sta, "year": year,
                "action": "promoted",
                "summary": cs,
                "elapsed_s": commit_r["elapsed_s"],
                "ts": utc_now(),
            })
            handled.add(rid)

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

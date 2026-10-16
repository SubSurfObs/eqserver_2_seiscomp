#!/usr/bin/env python3
"""qa_driver — worker-pool driver for the QA verification campaign.

Consumes the qa_sampler work queue, runs scan/qa_verify.verify_day in a
multiprocessing pool, and appends one JSON line per completed day to
<out-dir>/results.jsonl, flushed per item (scans write at intervals).

Safety: refuses any --out-dir outside /mnt/seiscomp_staging/qa_verify/
unless --allow-out-dir is passed (for local smoke tests). Writes ONLY the
results file, a heartbeat log line per batch, and a provenance header.
Resume-by-default: (net, sta, date) already in results.jsonl are skipped.
--max-hours is a hard backstop so a wedged run can never outlive its window.
"""

import argparse
import json
import multiprocessing as mp
import socket
import subprocess
import sys
import time
from datetime import date as date_cls, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_verify  # noqa: E402

ALLOWED_PREFIX = "/mnt/seiscomp_staging/qa_verify/"
# Resume treats these as non-terminal: the day is re-queued on restart.
RETRY_CATEGORIES = {"comparator_error", "no_station_db"}

_G = {}


def _init(db_dirs, sds_root, repo_root):
    _G["db_dirs"] = [Path(d).expanduser() for d in db_dirs]
    _G["sds_root"] = Path(sds_root)
    _G["registry"] = qa_verify.load_registry(Path(repo_root))


def _work(item):
    t0 = time.time()
    rec = qa_verify.verify_day(
        item["net"], item["sta"], date_cls.fromisoformat(item["date"]),
        _G["db_dirs"], _G["sds_root"], _G["registry"],
        stratum=item.get("stratum", "uniform"))
    rec["elapsed_s"] = round(time.time() - t0, 1)
    return rec


def git_sha(repo: Path) -> str:
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queue", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--db-dirs", nargs="*", default=qa_verify.DEFAULT_DB_DIRS)
    ap.add_argument("--sds-root", default="/mnt/seiscomp_archive")
    ap.add_argument("--repo-root",
                    default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-hours", type=float, default=24.0)
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N new items (0 = no limit)")
    ap.add_argument("--allow-out-dir", action="store_true",
                    help="permit an out-dir outside the QA subtree (tests)")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="permit uncommitted scan/ changes (tests only)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    if not str(out_dir).startswith(ALLOWED_PREFIX) and not args.allow_out_dir:
        sys.exit(f"REFUSED: out-dir {out_dir} outside {ALLOWED_PREFIX} "
                 f"(pass --allow-out-dir only for tests)")
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    done = set()
    if results_path.exists():
        with open(results_path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("t") != "day":
                    continue
                cats = {x.get("category") for x in r.get("findings", [])}
                if cats & RETRY_CATEGORIES:
                    continue  # non-terminal: retry on resume
                done.add((r["net"], r["sta"], r["date"]))

    queue = []
    with open(args.queue) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if (item["net"], item["sta"], item["date"]) not in done:
                queue.append(item)
    if args.limit:
        queue = queue[:args.limit]

    repo_root = Path(args.repo_root)
    scan_dirty = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--", "scan/"],
        capture_output=True, text=True).stdout.strip()
    if scan_dirty and not args.allow_dirty:
        sys.exit("REFUSED: scan/ has uncommitted changes (provenance); "
                 "commit them or pass --allow-dirty for local tests:\n"
                 + scan_dirty)
    header = {
        "t": "header", "ts": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(), "argv": sys.argv[1:],
        "n_already_done": len(done), "n_todo": len(queue),
        "eqserver_git": git_sha(repo_root),
        "sudspy_git": git_sha(repo_root.parent / "sudspy"),
    }
    deadline = time.time() + args.max_hours * 3600

    with open(results_path, "a") as out, \
         mp.Pool(args.workers, initializer=_init,
                 initargs=(args.db_dirs, args.sds_root,
                           args.repo_root)) as pool:
        out.write(json.dumps(header) + "\n")
        out.flush()
        n = n_find = 0
        t_start = time.time()
        it = pool.imap_unordered(_work, queue, chunksize=1)
        while True:
            try:
                rec = it.next(timeout=120)  # bounded wait: deadline holds
            except mp.TimeoutError:         # even if a worker hangs on NFS
                if time.time() > deadline:
                    print("max-hours reached while waiting; terminating",
                          flush=True)
                    pool.terminate()
                    break
                continue
            except StopIteration:
                break
            out.write(json.dumps(rec) + "\n")
            out.flush()
            n += 1
            if rec.get("finding_counts") or rec.get("findings"):
                n_find += 1
            if n % 20 == 0 or time.time() > deadline:
                rate = n / max(time.time() - t_start, 1)
                print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                      f"{n}/{len(queue)} done, {n_find} with findings, "
                      f"{rate*3600:.0f}/h", flush=True)
            if time.time() > deadline:
                print("max-hours reached; stopping (resume to continue)",
                      flush=True)
                pool.terminate()
                break
        out.write(json.dumps({
            "t": "footer", "ts": datetime.now(timezone.utc).isoformat(),
            "n_done": n, "n_with_findings": n_find}) + "\n")
    print(f"done: {n} items, {n_find} with findings -> {results_path}")


if __name__ == "__main__":
    main()

"""run_recovery_register.py — drive phase3 re-runs for the recovery register.

Reads docs/recovery_register.yaml. For each entry not yet completed (tracked
via convert_done.jsonl with `recovery: true` marker), runs phase3 over the
specified date range, then appends a convert_done.jsonl entry so promote.py
picks it up automatically within its poll window.

Sequential by design — phase3 invocations are not parallelised, both because
each phase3 already uses workers=8 internally and because we want the
operator to see each unit land before the next one starts.

Usage:

    python3 scan/run_recovery_register.py \\
        --register docs/recovery_register.yaml \\
        --staging-sds /mnt/seiscomp_staging/seiscomp_archive \\
        --queue-dir  /mnt/seiscomp_staging/eqserver_sweep \\
        --registry   metadata/station_registry.yaml \\
        --workers    8 \\
        --commit

By default this is a DRY-RUN — it prints what it would do without running
phase3. Pass `--commit` to actually invoke phase3 and append queue entries.

Resume: the script reads convert_done.jsonl on startup; entries with
`recovery: true` matching the (sta, year, start_date, end_date) tuple are
skipped. Safe to re-run after a kill.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_REGISTER = REPO_ROOT / "docs" / "recovery_register.yaml"
DEFAULT_STATION_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
DEFAULT_DISK_TO_SDS = Path(
    "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/scripts"
)
DEFAULT_PY = Path(
    "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"
)
PHASE3 = SCRIPT_DIR / "phase3_driver.py"


def utc_now_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_register(path: Path) -> list[dict]:
    """Load the recovery register YAML. Validates required fields per entry."""
    with path.open() as f:
        doc = yaml.safe_load(f) or {}
    entries = doc.get("entries", [])
    required = {"sta", "year", "start_date", "end_date"}
    for i, e in enumerate(entries):
        missing = required - set(e)
        if missing:
            sys.exit(f"register entry {i} missing fields: {missing}")
    return entries


def load_done_recovery_keys(convert_done_path: Path) -> set[tuple]:
    """Read convert_done.jsonl and return the set of (sta, year, start, end)
    tuples that already have a successful recovery entry.

    Recovery entries are tagged `recovery: true` so we can distinguish them
    from regular sweep entries and from the LRNW _recovered_ manifest re-
    constructions (which used `recovered: true`, a different marker).
    """
    if not convert_done_path.exists():
        return set()
    done = set()
    with convert_done_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not e.get("recovery"):
                continue
            done.add((
                e.get("sta"),
                e.get("year"),
                e.get("recovery_start_date"),
                e.get("recovery_end_date"),
            ))
    return done


def build_phase3_cmd(
    py: Path, phase3: Path, station_db: Path, plan: Path, registry: Path,
    staging_sds: Path, disk_to_sds: Path,
    start_date: str, end_date: str,
    run_manifest: Path, workers: int,
) -> list[str]:
    """Assemble the phase3 invocation. Mirrors the sweep's convert.py shape
    but narrowed to a specific date range."""
    return [
        str(py), "-u", str(phase3),
        str(station_db), str(plan),
        "--registry", str(registry),
        "--staging-sds", str(staging_sds),
        "--disk-to-sds", str(disk_to_sds),
        "--workers", str(workers),
        "--commit",
        "--start-date", start_date,
        "--end-date", end_date,
        "--run-manifest", str(run_manifest),
    ]


def run_entry(
    entry: dict, args, queue_dir: Path, log_dir: Path,
) -> bool:
    """Run phase3 for one register entry. Returns True on success, False
    otherwise. On success, appends a convert_done.jsonl entry for promote.py.
    """
    sta = entry["sta"]
    year = int(entry["year"])
    start_date = entry["start_date"]
    end_date = entry["end_date"]
    notes = (entry.get("notes") or "").strip()

    plan = args.plans_dir / f"VW.{sta}.plan.yaml"
    station_db = args.station_db_dir / f"VW.{sta}.db"
    if not plan.exists():
        print(f"  SKIP — plan not found: {plan}", file=sys.stderr)
        return False
    if not station_db.exists():
        print(f"  SKIP — station db not found: {station_db}", file=sys.stderr)
        return False

    ts = utc_now_ts()
    run_id = f"eqserver_VW_{sta}_{year}_recovery_{ts}"
    run_manifest = (
        queue_dir / "run_manifests" / f"{run_id}.json"
    )
    log_path = log_dir / f"{run_id}.log"
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = build_phase3_cmd(
        py=args.python, phase3=PHASE3,
        station_db=station_db, plan=plan,
        registry=args.registry,
        staging_sds=args.staging_sds,
        disk_to_sds=args.disk_to_sds,
        start_date=start_date, end_date=end_date,
        run_manifest=run_manifest, workers=args.workers,
    )

    print(f"\n  {sta} {year} {start_date}..{end_date}")
    print(f"    run_id: {run_id}")
    print(f"    manifest: {run_manifest}")
    print(f"    log:      {log_path}")
    if notes:
        print(f"    notes: {notes.splitlines()[0]}")

    if not args.commit:
        print("    DRY-RUN — would execute:")
        print(f"      {' '.join(cmd)}")
        return False  # don't count as done in dry-run

    t0 = time.time()
    with log_path.open("w") as logf:
        result = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT)
    elapsed = time.time() - t0
    rc = result.returncode
    print(f"    rc={rc}  elapsed={elapsed:.0f}s")

    if rc != 0:
        print(f"    FAIL — phase3 returned non-zero. Inspect {log_path}",
              file=sys.stderr)
        return False
    if not run_manifest.exists():
        print(f"    FAIL — phase3 finished but no manifest at {run_manifest}",
              file=sys.stderr)
        return False

    # Append convert_done.jsonl entry so promote.py picks it up.
    convert_done = queue_dir / "convert_done.jsonl"
    convert_done.parent.mkdir(parents=True, exist_ok=True)
    entry_out = {
        "net": "VW",
        "sta": sta,
        "year": year,
        "run_id": run_id,
        "run_manifest_path": str(run_manifest),
        "staging_root": str(args.staging_sds),
        "ts": utc_now_iso(),
        "elapsed_s": round(elapsed, 1),
        "recovery": True,
        "recovery_start_date": start_date,
        "recovery_end_date": end_date,
        "recovery_source_register": str(args.register),
    }
    line = json.dumps(entry_out, separators=(",", ":"), sort_keys=True)
    with convert_done.open("a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())

    # Also append a "resolved" event to convert_failed.jsonl so any tooling
    # that walks convert_failed for "what's still unresolved?" can fold this
    # recovery in. The original failed event's run_id (if any) is best-effort
    # — we look it up by (sta, year) and pick the latest failed event lacking
    # a matching resolved event. This is the queue-reconciliation fix per
    # Option B (see CLAUDE.md "Sweep recovery registers" + sweep_status.py
    # --unit-table). Single-host write (recovery script runs on staging VM,
    # same host as convert.py) so cross-host append-atomicity is not at
    # stake.
    convert_failed = queue_dir / "convert_failed.jsonl"
    original_run_id = None
    if convert_failed.exists():
        failed_for_unit = []
        resolved_run_ids = set()
        for fl in convert_failed.open():
            try:
                d = json.loads(fl.strip())
            except Exception:
                continue
            if d.get("net") == "VW" and d.get("sta") == sta and d.get("year") == year:
                if d.get("action") == "resolved":
                    resolved_run_ids.add(d.get("original_run_id"))
                else:
                    failed_for_unit.append(d)
        for d in reversed(failed_for_unit):
            if d.get("run_id") and d["run_id"] not in resolved_run_ids:
                original_run_id = d["run_id"]
                break
    resolved_event = {
        "action": "resolved",
        "net": "VW",
        "sta": sta,
        "year": year,
        "original_run_id": original_run_id,
        "recovery_run_id": run_id,
        "ts": utc_now_iso(),
        "recovery_source_register": str(args.register),
    }
    resolved_line = json.dumps(resolved_event, separators=(",", ":"), sort_keys=True)
    with convert_failed.open("a") as f:
        f.write(resolved_line + "\n")
        f.flush()
        os.fsync(f.fileno())

    print(f"    OK — convert_done appended; promote.py will pick up in <60s")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--register", type=Path, default=DEFAULT_REGISTER,
                    help=f"Recovery register YAML (default: {DEFAULT_REGISTER})")
    ap.add_argument("--staging-sds", type=Path, required=True)
    ap.add_argument("--queue-dir", type=Path, required=True,
                    help="e.g. /mnt/seiscomp_staging/eqserver_sweep")
    ap.add_argument("--registry", type=Path, required=True,
                    help="metadata/station_registry.yaml")
    ap.add_argument("--plans-dir", type=Path, default=None,
                    help="default: <queue-dir>/plans/VW")
    ap.add_argument("--station-db-dir", type=Path, default=DEFAULT_STATION_DB_DIR)
    ap.add_argument("--disk-to-sds", type=Path, default=DEFAULT_DISK_TO_SDS)
    ap.add_argument("--python", type=Path, default=DEFAULT_PY)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--log-dir", type=Path,
                    default=Path("/var/tmp/eqserver_recovery_logs"))
    ap.add_argument("--commit", action="store_true",
                    help="Actually run phase3. Without this flag, prints "
                         "the commands but does NOT execute them.")
    ap.add_argument("--only", action="append", default=[],
                    metavar="STA_YEAR",
                    help="Run only entries matching STA_YEAR (e.g. DDNE_2017). "
                         "Can be passed multiple times.")
    args = ap.parse_args()

    if args.plans_dir is None:
        args.plans_dir = args.queue_dir / "plans" / "VW"

    # Sanity checks.
    for p, desc in [
        (args.register, "register YAML"),
        (args.staging_sds, "staging SDS root"),
        (args.queue_dir, "queue dir"),
        (args.registry, "station registry"),
        (args.plans_dir, "plans dir"),
        (args.station_db_dir, "station DB dir"),
        (args.disk_to_sds, "disk_to_sds engine path"),
        (args.python, "python interpreter"),
        (PHASE3, "phase3_driver.py"),
    ]:
        if not p.exists():
            sys.exit(f"ERROR: {desc} not found: {p}")

    register = load_register(args.register)
    convert_done = args.queue_dir / "convert_done.jsonl"
    done_keys = load_done_recovery_keys(convert_done)

    print(f"[recovery] host: {socket.gethostname()}")
    print(f"[recovery] register: {args.register} ({len(register)} entries)")
    print(f"[recovery] already-done recovery entries: {len(done_keys)}")
    print(f"[recovery] mode: {'COMMIT' if args.commit else 'DRY-RUN'}")
    if args.only:
        print(f"[recovery] --only filter: {args.only}")

    pending = []
    for e in register:
        sta = e["sta"]
        year = int(e["year"])
        key = (sta, year, e["start_date"], e["end_date"])
        tag = f"{sta}_{year}"
        if args.only and tag not in args.only:
            continue
        if key in done_keys:
            print(f"  {tag} {e['start_date']}..{e['end_date']} — already done, skipping")
            continue
        pending.append(e)

    print(f"\n[recovery] pending entries: {len(pending)}")

    n_ok = 0
    n_fail = 0
    for e in pending:
        ok = run_entry(e, args, args.queue_dir, args.log_dir)
        if ok:
            n_ok += 1
        else:
            n_fail += 1

    print(f"\n[recovery] done. ok={n_ok} fail={n_fail} pending_skipped={len(pending) - n_ok - n_fail}")
    if not args.commit and pending:
        print("[recovery] (dry-run; pass --commit to actually execute)")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

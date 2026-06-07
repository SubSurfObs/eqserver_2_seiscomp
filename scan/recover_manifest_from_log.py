"""Reconstruct a phase3 run_manifest from its log + staging file inventory.

WHY: phase3's multiprocessing.Pool teardown occasionally hangs *after* every
day-job has written its SDS records but *before* the run_manifest is emitted
(seen on DDWB 2022, LOYU 2016, LRNW 2020). The watchdog kills phase3, the
sweep advances, but the staged year sits orphaned — apply.py needs a manifest
to promote anything.

This tool rebuilds the manifest from two sources that survive the kill:

    1. The phase3 log — has the run_id, policy_sha, project_git, and a
       per-day status line ("[YYYY-MM-DD] ok files=1440 bf=1 traces=N rate=X
       errs=0 ...") for every day the workers completed.
    2. The staging tree — has the actual SDS day-files, from which we
       reconstruct bytes_written per date.

The reconstructed manifest matches the schema of a normal phase3 run_manifest
closely enough for apply.py to consume it: top-level identity fields lifted
verbatim from the log banner, per_date_status synthesized from log lines
cross-checked against disk presence.

Usage:

    python3 scan/recover_manifest_from_log.py \\
        --log /var/tmp/.../eqserver_VW_LRNW_2020_<TS>.log \\
        --staging-sds /mnt/seiscomp_staging/seiscomp_archive \\
        --net VW --sta LRNW --year 2020 \\
        --out /mnt/seiscomp_staging/eqserver_sweep/run_manifests/eqserver_VW_LRNW_2020_<TS>.json

Run with --dry-run first to print the manifest to stdout for review.

Safety: refuses to overwrite an existing manifest unless --force is passed.
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Optional


# === Log parsing =============================================================

BANNER_RUN_ID = re.compile(r"^\[phase3\]\s+run_id=(\S+)")
BANNER_POLICY_SHA = re.compile(r"^\[phase3\]\s+policy_sha=([0-9a-f]+)")
BANNER_PROJECT_GIT = re.compile(r"^\[phase3\]\s+project_git=([0-9a-f]+)")
BANNER_RUN_MANIFEST = re.compile(r"^\[phase3\]\s+run-manifest target:\s+(\S+)")
BANNER_STATION = re.compile(
    r"^\[phase3\]\s+station=(\S+)\.(\S+)\s+loc='(\S*)'\s+status=(\S+)"
)

# Per-day line:
#   "  [2020-12-31] ok         files= 1440 bf=1 traces= 84 rate=250.0
#    errs=0 recovered=0 dropped=[] bogus_yr=0 WRITE=3"
DAY_LINE = re.compile(
    r"^\s*\[(\d{4})-(\d{2})-(\d{2})\]\s+(\S+)\s+"
    r"files=\s*(\d+)\s+bf=(\d+)\s+traces=\s*(\d+)\s+rate=([\d.]+)\s+"
    r"errs=(\d+)"
)


def parse_log(log_path: Path) -> dict:
    """Parse the phase3 log for banner identity + per-day status entries.

    Returns a dict with keys:
        run_id, policy_sha, project_git, run_manifest_path,
        net, sta, location, banner_status, per_day
    where per_day is a list of dicts with keys:
        date (YYYY-MM-DD), status, n_files, n_boundary_files,
        n_traces, rate_hz, read_errors
    """
    out = {
        "run_id": None,
        "policy_sha": None,
        "project_git": None,
        "run_manifest_path": None,
        "net": None,
        "sta": None,
        "location": None,
        "banner_status": None,
        "per_day": [],
    }
    seen_dates = set()
    with log_path.open() as f:
        for line in f:
            if m := BANNER_RUN_ID.match(line):
                out["run_id"] = m.group(1)
                continue
            if m := BANNER_POLICY_SHA.match(line):
                out["policy_sha"] = m.group(1)
                continue
            if m := BANNER_PROJECT_GIT.match(line):
                out["project_git"] = m.group(1)
                continue
            if m := BANNER_RUN_MANIFEST.match(line):
                out["run_manifest_path"] = m.group(1)
                continue
            if m := BANNER_STATION.match(line):
                out["net"], out["sta"], out["location"], out["banner_status"] = (
                    m.group(1), m.group(2), m.group(3), m.group(4),
                )
                continue
            if m := DAY_LINE.match(line):
                y, mo, d, status, nf, bf, ntr, rate, errs = m.groups()
                date = f"{y}-{mo}-{d}"
                if date in seen_dates:
                    # Defensive: don't double-count if a day-line appears
                    # twice (shouldn't happen in normal runs).
                    continue
                seen_dates.add(date)
                out["per_day"].append({
                    "date": date,
                    "status": status,
                    "n_files": int(nf),
                    "n_boundary_files": int(bf),
                    "n_traces": int(ntr),
                    "rate_hz": float(rate),
                    "read_errors": int(errs),
                })
    return out


# === Staging inventory =======================================================


def inventory_staging(staging_sds: Path, net: str, sta: str, year: int) -> dict:
    """Walk the staging SDS tree for (net, sta, year) and return:

        { "YYYY-MM-DD": total_bytes_across_channels, ... }

    Empty dict if no staging dir exists. Skips files with unparseable names.
    """
    by_date = {}
    sta_root = staging_sds / str(year) / net / sta
    if not sta_root.exists():
        return by_date
    fname_re = re.compile(
        rf"^{net}\.{sta}\.\S*\.\S+\.D\.{year}\.(\d{{3}})$"
    )
    for cha_dir in sorted(sta_root.iterdir()):
        if not cha_dir.is_dir():
            continue
        for f in sorted(cha_dir.iterdir()):
            if not f.is_file():
                continue
            m = fname_re.match(f.name)
            if not m:
                continue
            jday = int(m.group(1))
            try:
                date = (_dt.date(year, 1, 1) +
                        _dt.timedelta(days=jday - 1)).isoformat()
            except ValueError:
                continue
            by_date[date] = by_date.get(date, 0) + f.stat().st_size
    return by_date


# === Manifest synthesis ======================================================


def synthesize_per_date(per_day_log, bytes_by_date) -> tuple[list, dict]:
    """Cross-reference log per-day entries with staging file presence.

    Returns (per_date_status_list, aggregate_dict). For each day-line in the
    log:
      * If staging has bytes for that date: emit "ok" with real bytes_written.
      * If staging has nothing: mark "error" with bytes_written=0 so apply.py
        skips it. (We don't know why the bytes are missing — the kill, a
        zero-trace day, or something else — but apply.py should not try to
        promote a non-existent file.)
    """
    items_attempted = len(per_day_log)
    items_succeeded = 0
    items_failed = 0
    total_bytes = 0
    out = []
    for d in per_day_log:
        date = d["date"]
        b = bytes_by_date.get(date, 0)
        status = d["status"]
        error = ""
        if status == "ok" and b == 0:
            status = "error"
            error = "expected staging file(s) not found at recovery time"
            items_failed += 1
        elif status == "ok":
            items_succeeded += 1
            total_bytes += b
        else:
            # Non-"ok" status from the log (e.g. error): preserve, accumulate.
            items_failed += 1
        out.append({
            "bulk_fallback_used": False,
            "bytes_written": b,
            "date": date,
            "error": error,
            "n_boundary_files": d["n_boundary_files"],
            "n_files": d["n_files"],
            "n_traces": d["n_traces"],
            "rate_hz": d["rate_hz"],
            "read_errors": d["read_errors"],
            "status": status,
        })
    aggregate = {
        "bytes_written": total_bytes,
        "items_attempted": items_attempted,
        "items_failed": items_failed,
        "items_succeeded": items_succeeded,
    }
    return out, aggregate


def build_manifest(
    log_banner: dict, per_date_status: list, aggregate: dict,
    net: str, sta: str, year: int,
    staging_sds: Path, plan_path: Path,
    started_at: Optional[str], finished_at: Optional[str],
    classifier_version: str,
    phase3_invocation_log_path: Path,
) -> dict:
    """Assemble the final manifest dict. Mirrors the schema of a normal phase3
    run_manifest as closely as possible.

    Top-level fields lifted verbatim from log_banner where available.
    started_at synthesized from the run_id timestamp tail. finished_at
    defaults to "now" (when this recovery ran).
    """
    if not started_at:
        # Pull from run_id tail: "eqserver_VW_LRNW_20260606T221558Z"
        rid = log_banner["run_id"] or ""
        m = re.search(r"(\d{8}T\d{6}Z)$", rid)
        if m:
            ts = m.group(1)
            started_at = (
                f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T"
                f"{ts[9:11]}:{ts[11:13]}:{ts[13:15]}Z"
            )
        else:
            started_at = ""
    if not finished_at:
        finished_at = _dt.datetime.now(tz=_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    days_ok = sum(1 for r in per_date_status if r["status"] == "ok")
    elapsed_s = 0
    if started_at and finished_at:
        try:
            t0 = _dt.datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
            t1 = _dt.datetime.strptime(finished_at, "%Y-%m-%dT%H:%M:%SZ")
            elapsed_s = max(0, int((t1 - t0).total_seconds()))
        except ValueError:
            elapsed_s = 0
    throughput = (days_ok / elapsed_s) if elapsed_s > 0 else 0.0
    return {
        "aggregate": aggregate,
        "classifier_version": classifier_version,
        "eqserver": {
            "days_no_files": [],
            "elapsed_s": elapsed_s,
            "flagged_days_skipped": [],
            "per_date_status": per_date_status,
            "read_errors": [],
            "throughput_days_per_s": throughput,
            "recovered_from_log": True,
            "recovered_from_log_path": str(phase3_invocation_log_path),
        },
        "finished_at": finished_at,
        "host": socket.gethostname(),
        "kind": "eqserver",
        "net": net,
        "operator": os.environ.get("USER", "unknown"),
        "phase3_invocation": (
            f"recovered_from_log:{phase3_invocation_log_path}"
        ),
        "policy_sha": log_banner["policy_sha"],
        "policy_yaml_path": str(plan_path),
        "project": "eqserver_2_seiscomp",
        "project_git": log_banner["project_git"],
        "run_id": log_banner["run_id"],
        "sta": sta,
        "started_at": started_at,
        "target_root": str(staging_sds),
    }


# === CLI =====================================================================


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", required=True, type=Path,
                    help="Path to the phase3 log file to parse.")
    ap.add_argument("--staging-sds", required=True, type=Path,
                    help="Staging SDS root, e.g. /mnt/seiscomp_staging/seiscomp_archive.")
    ap.add_argument("--net", required=True, help="Network code, e.g. VW.")
    ap.add_argument("--sta", required=True, help="Station code, e.g. LRNW.")
    ap.add_argument("--year", required=True, type=int)
    ap.add_argument("--plan", type=Path,
                    help="Path to the plan YAML (default: "
                         "/mnt/seiscomp_staging/eqserver_sweep/plans/<NET>/<NET>.<STA>.plan.yaml).")
    ap.add_argument("--out", type=Path,
                    help="Output manifest path. Default: derive from log filename.")
    ap.add_argument("--classifier-version", default="v3-OptionB")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print manifest to stdout instead of writing.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite --out if it already exists.")
    args = ap.parse_args()

    if not args.log.exists():
        sys.exit(f"ERROR: log not found: {args.log}")
    if not args.staging_sds.exists():
        sys.exit(f"ERROR: staging-sds not found: {args.staging_sds}")

    plan = args.plan or (
        Path("/mnt/seiscomp_staging/eqserver_sweep/plans") /
        args.net / f"{args.net}.{args.sta}.plan.yaml"
    )
    if not plan.exists():
        sys.exit(f"ERROR: plan YAML not found: {plan}")

    print(f"[recover] parsing log: {args.log}", file=sys.stderr)
    banner = parse_log(args.log)
    if not banner["run_id"]:
        sys.exit("ERROR: no run_id banner found in log — wrong file?")
    if not banner["policy_sha"]:
        sys.exit("ERROR: no policy_sha banner found in log.")
    print(f"[recover]   run_id={banner['run_id']}", file=sys.stderr)
    print(f"[recover]   policy_sha={banner['policy_sha']}", file=sys.stderr)
    print(f"[recover]   project_git={banner['project_git']}", file=sys.stderr)
    print(f"[recover]   day-lines parsed: {len(banner['per_day'])}", file=sys.stderr)

    # Sanity: net/sta from banner must match args.
    if banner["net"] and banner["net"] != args.net:
        sys.exit(f"ERROR: log net={banner['net']} != arg --net={args.net}")
    if banner["sta"] and banner["sta"] != args.sta:
        sys.exit(f"ERROR: log sta={banner['sta']} != arg --sta={args.sta}")

    print(f"[recover] walking staging tree: "
          f"{args.staging_sds}/{args.year}/{args.net}/{args.sta}", file=sys.stderr)
    bytes_by_date = inventory_staging(args.staging_sds, args.net, args.sta, args.year)
    print(f"[recover]   dates with bytes on disk: {len(bytes_by_date)}",
          file=sys.stderr)

    per_date, aggregate = synthesize_per_date(banner["per_day"], bytes_by_date)
    print(f"[recover]   per_date entries: {len(per_date)} "
          f"(ok={aggregate['items_succeeded']} "
          f"fail={aggregate['items_failed']} "
          f"bytes={aggregate['bytes_written']:,})", file=sys.stderr)

    manifest = build_manifest(
        log_banner=banner,
        per_date_status=per_date,
        aggregate=aggregate,
        net=args.net, sta=args.sta, year=args.year,
        staging_sds=args.staging_sds,
        plan_path=plan,
        started_at=None,
        finished_at=None,
        classifier_version=args.classifier_version,
        phase3_invocation_log_path=args.log,
    )

    if args.dry_run:
        json.dump(manifest, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    out_path = args.out or (
        Path("/mnt/seiscomp_staging/eqserver_sweep/run_manifests") /
        (args.log.stem + ".json")
    )
    if out_path.exists() and not args.force:
        sys.exit(f"ERROR: {out_path} exists; pass --force to overwrite.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".partial")
    with tmp.open("w") as f:
        json.dump(manifest, f, indent=2)
    tmp.rename(out_path)
    print(f"[recover] wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

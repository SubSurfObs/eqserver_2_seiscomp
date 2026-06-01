#!/usr/bin/env python3
"""Phase 3 per-station EchoPro driver.

Consumes a station plan YAML (from plan_generator.py) plus the Level-1 manifest,
dispatches each "clean" day through disk_to_sds/scripts/suds_convert.py, and
writes the result to a staging SDS root.

Dry-run by default (mirrors sds_staging_ledger/apply.py). Pass --commit to
actually write SDS files.

Coverage of recorders in v1:
  - echopro: full pipeline (sudspy -> convert_suds_files -> write_sds)
  - gecko / reftek_rt130 / minimus / piesmo: stubbed; v2 deliverables.
  - defer_conversion: no-op with an explanatory log line.

Per-day flow:
  1. Query manifest for the day's source files (disk + telemetry, not excluded)
  2. Call convert_suds_files(files, network, station) / equivalent
  3. Call write_sds(stream, staging_sds_root)  # only if --commit
  4. Log per-day result (n_files, n_traces, sds_files_written, qc.read_errors)

  Every day in the epoch range is ATTEMPTED. The plan's `flagged_days` list
  is descriptive QA metadata, NOT a skip signal — see
  [[feedback-convert-what-is-on-disc]] in agent memory. Days that genuinely
  cannot be read return status=no_files or status=parse_error from the
  worker; these are runtime-detected pathologies, not pre-filtered classifier
  decisions.

Run:
  python3 scan/phase3_driver.py <manifest.db> <plan.yaml> [--commit] \\
      --staging-sds /mnt/seiscomp_staging/seiscomp_archive \\
      --registry metadata/station_registry.yaml \\
      [--limit-days N] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess as _sp
import sys
import time
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cross_source import select_files_for_day, DEFAULT_DISK_SIZE_FLOOR_RATIO  # noqa: E402

# disk_to_sds engine on the VM. Path is resolved at import time so the user
# can override via PYTHONPATH or the --disk-to-sds flag.
SUDS_CONVERT_PATH_DEFAULT = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/scripts"


DEFAULT_MIN_DATA_YEAR = 2012   # UoM VW/VX/DU operations didn't start before this
                                # — anything before is no-GPS-lock or bogus header


def _filter_bogus_year_traces(stream, min_year, dropped_list):
    """Drop traces whose start-time year is below `min_year` (no-GPS-lock /
    bogus-SUDS-header guard).

    Some EqServer-era recorder files have valid path-dates (e.g.
    /BEST/continuous/2024/.../) but contain SUDS traces whose internal
    starttime fell back to a pre-GPS-fix default (often 1989, 1970, 1999).
    If those traces flow through to write_sds(), they land in SDS day-files
    under /staging/1989/... — wrong year subtree, almost certainly bogus
    data.

    This filter inspects each trace's `stats.starttime.year` and keeps only
    those at or above the cutoff. Dropped traces are appended to
    `dropped_list` for surfacing in the day-job result.

    Operates in-place on the passed `Stream`; returns it for chaining.
    """
    from obspy import Stream
    if not stream or min_year is None:
        return stream
    kept = []
    for tr in stream:
        yr = tr.stats.starttime.year
        if yr < min_year:
            dropped_list.append({
                'id': tr.id,
                'starttime': str(tr.stats.starttime),
                'npts': int(tr.stats.npts),
                'reason': f'starttime year {yr} < min_data_year {min_year}',
            })
        else:
            kept.append(tr)
    # Return a fresh Stream rather than mutating the input — caller assigns.
    return Stream(kept)


def _write_sds_retry(suds_convert, stream, staging_root, retries=3, backoff=2.0):
    """Write SDS with retry on transient OSError.

    The staging SMB mount is `soft` (verified 2026-05-28), so a brief network
    blip surfaces as OSError to Python rather than a transparent kernel retry.
    The write itself is atomic (.partial -> rename in suds_convert.write_sds),
    so a failed attempt leaves no half-file; we just retry. Re-raises after
    `retries` exhausted so the day is recorded as an error (resumable).
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            return suds_convert.write_sds(stream, staging_root)
        except OSError as e:
            last = e
            if attempt < retries:
                time.sleep(backoff * attempt)  # linear backoff: 2s, 4s, ...
    raise last


def _concat_zip_members(files, combined, read_errors):
    """Read each .ms.zip's inner mseed and append its bytes to `combined`.

    Reads the whole .ms.zip in ONE sequential pass, then opens the ZIP from
    memory. The ZIP format needs random-access seeks (End-Of-Central-Directory
    at the tail, then back to the member); on an NFS file handle each seek is a
    round-trip. Reading the (tiny, ~12-70 KB) whole file once collapses that to
    a single round-trip — ~2x faster at workers=1 on cold NFS, which matters
    most for the Gecko/Minimus cohorts (the slow ones). Used by both the gecko
    and minimus branches so the I/O pattern is identical.
    """
    import io as _io
    import zipfile as _zip
    for path in files:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()                       # one sequential NFS read
            with _zip.ZipFile(_io.BytesIO(raw)) as z:  # seeks now in memory
                for name in z.namelist():
                    if name.endswith((".ms", ".mseed")):
                        combined.write(z.read(name))
                        break
        except Exception as e:
            read_errors.append((path, f"{type(e).__name__}: {e}"))


def _iter_days(start_date, end_date):
    """Inclusive day-by-day iterator."""
    d = start_date
    while d <= end_date:
        yield d
        d += timedelta(days=1)


def query_day_files(conn, station, d, recorder):
    """Return disk files for (station, dir_year/month/day) of given recorder,
    not excluded. Caller chooses recorder ('echopro' or 'gecko'). Legacy
    disk-only path — kept for back-compat with the v1 dispatch."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
        "  AND recorder_type = ? AND source_type = 'disk' "
        "  AND exclude_reason IS NULL "
        "ORDER BY path",
        (station, d.year, d.month, d.day, recorder),
    ).fetchall()
    return [r[0] for r in rows]


def query_cross_source_day_files(conn, station, d, recorder, disk_size_floor_ratio):
    """Return the cross-source-selected file paths for one station-day.

    Pulls BOTH disk and telemetry candidates from the manifest, then runs
    `cross_source.select_files_for_day` (pure function in scan/cross_source.py)
    to apply the per-HHMM dedup + disk-preference-with-threshold policy.

    Recorder-aware on what counts as "data":
      - echopro/gecko: include only rows with exclude_reason IS NULL
      - minimus: include rows with exclude_reason='single_channel'
        (the per-channel files that the default parser excludes ARE the data
        for Minimus stations)
    """
    if recorder == "minimus":
        sql = (
            "SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
            "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
            "  AND recorder_type = 'mseed' AND exclude_reason = 'single_channel'"
        )
        params = (station, d.year, d.month, d.day)
    else:
        sql = (
            "SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
            "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
            "  AND recorder_type = ? AND exclude_reason IS NULL"
        )
        params = (station, d.year, d.month, d.day, recorder)
    rows = conn.execute(sql, params).fetchall()
    return select_files_for_day(rows, disk_size_floor_ratio=disk_size_floor_ratio)


def convert_gecko_day(station, network, location, files, staging_sds_root, commit, suds_convert):
    """Gecko branch: read all .ms.zip in day, concat the inner mseed records into
    one buffer, parse with ObsPy, override network+location, merge, write SDS.

    Gecko's mseed headers already carry correct channel (CHZ/CHN/CHE) and station;
    only network (UM placeholder) and location (empty '') need patching. The
    location value comes from the plan YAML (registry-driven; default "00").
    STEIM2 encoding is preserved end-to-end (no decode-recode).
    """
    import io, zipfile
    from obspy import read, Stream
    if not files:
        return {"status": "no_files", "n_files": 0}
    combined = io.BytesIO()
    read_errors = []
    _concat_zip_members(files, combined, read_errors)
    combined.seek(0)
    try:
        st = read(combined, format="MSEED")
    except Exception as e:
        return {"status": "parse_error", "n_files": len(files),
                "error": f"{type(e).__name__}: {e}", "read_errors": len(read_errors)}
    for tr in st:
        tr.stats.network = network
        tr.stats.location = location
    st.merge(method=1, fill_value=None)
    # split() breaks masked-array traces (with gaps) back into separate
    # non-masked traces — required because ObsPy MSEED writer rejects masked.
    st = st.split()
    st.sort(["starttime"])
    # Drop traces with bogus pre-2012 starttimes (no-GPS-lock guard).
    bogus = []
    st = _filter_bogus_year_traces(st, DEFAULT_MIN_DATA_YEAR, bogus)

    rate = st[0].stats.sampling_rate if len(st) > 0 else None
    result = {
        "status": "ok" if not read_errors else "qc_flagged",
        "n_files": len(files),
        "n_traces": len(st),
        "rate_hz": rate,
        "dropped_components": [],
        "read_errors": len(read_errors),
        "bogus_year_traces_dropped": len(bogus),
        "sds_files_written": [],
    }
    if commit and len(st) > 0:
        written = _write_sds_retry(suds_convert, st, staging_sds_root)
        result["sds_files_written"] = [(str(p), sz) for p, sz in written]
    elif len(st) > 0:
        from collections import defaultdict
        groups = defaultdict(int)
        for tr in st:
            p = suds_convert._sds_day_path(staging_sds_root, tr)
            groups[str(p)] += tr.stats.npts
        result["sds_files_planned"] = [(p, n) for p, n in sorted(groups.items())]
    return result


def query_minimus_day_files(conn, station, d):
    """Minimus per-channel files: classifier excludes them as 'single_channel',
    but for Minimus stations they ARE the data. ~4320 per day = 3 chan × 1440 min."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
        "  AND recorder_type = 'mseed' AND exclude_reason = 'single_channel' "
        "ORDER BY path",
        (station, d.year, d.month, d.day),
    ).fetchall()
    return [r[0] for r in rows]


def convert_minimus_day(station, network, location, files, staging_sds_root, commit, suds_convert):
    """Minimus branch: per-channel-per-minute mseed zips (~4320 files/day).
    Each zip holds one minute of one component. Same in-memory concat-then-parse
    pattern as Gecko; ObsPy groups by trace id automatically. Location override
    comes from the plan YAML (registry-driven; default "00").
    """
    import io, zipfile
    from obspy import read
    if not files:
        return {"status": "no_files", "n_files": 0}
    combined = io.BytesIO()
    read_errors = []
    _concat_zip_members(files, combined, read_errors)
    combined.seek(0)
    try:
        st = read(combined, format="MSEED")
    except Exception as e:
        return {"status": "parse_error", "n_files": len(files),
                "error": f"{type(e).__name__}: {e}", "read_errors": len(read_errors)}
    for tr in st:
        tr.stats.network = network
        tr.stats.location = location
    st.merge(method=1, fill_value=None)
    st = st.split()
    st.sort(["starttime"])
    # Drop traces with bogus pre-2012 starttimes (no-GPS-lock guard).
    bogus = []
    st = _filter_bogus_year_traces(st, DEFAULT_MIN_DATA_YEAR, bogus)

    rate = st[0].stats.sampling_rate if len(st) > 0 else None
    result = {
        "status": "ok" if not read_errors else "qc_flagged",
        "n_files": len(files),
        "n_traces": len(st),
        "rate_hz": rate,
        "dropped_components": [],
        "read_errors": len(read_errors),
        "bogus_year_traces_dropped": len(bogus),
        "sds_files_written": [],
    }
    if commit and len(st) > 0:
        written = _write_sds_retry(suds_convert, st, staging_sds_root)
        result["sds_files_written"] = [(str(p), sz) for p, sz in written]
    elif len(st) > 0:
        from collections import defaultdict
        groups = defaultdict(int)
        for tr in st:
            p = suds_convert._sds_day_path(staging_sds_root, tr)
            groups[str(p)] += tr.stats.npts
        result["sds_files_planned"] = [(p, n) for p, n in sorted(groups.items())]
    return result


def convert_echopro_day(suds_convert, station, network, location, files, staging_sds_root, commit):
    """One EchoPro station-day. Returns dict with n_files, n_traces, write_results, qc.

    suds_convert.convert_suds_files() resolves location via FDSN inventory if
    one is passed (inv=...); without an inventory it falls back to its own '00'
    default. We then enforce the registry-driven `location` on every trace
    afterwards so the plan YAML is the single source of truth for the SDS
    location code regardless of what the SUDS source said.
    """
    if not files:
        return {"status": "no_files", "n_files": 0}
    stream, qc = suds_convert.convert_suds_files(files, network=network, station=station)
    for tr in stream:
        tr.stats.location = location
    # Drop traces with bogus pre-2012 starttimes (no-GPS-lock guard).
    bogus = []
    stream = _filter_bogus_year_traces(stream, DEFAULT_MIN_DATA_YEAR, bogus)
    result = {
        "status": "ok" if not qc["read_errors"] else "qc_flagged",
        "n_files": len(files),
        "n_traces": qc["n_traces"],
        "rate_hz": qc["rate_hz"],
        "dropped_components": qc["dropped_components"],
        "read_errors": len(qc["read_errors"]),
        # n_recovered: files that parsed but stopped early (trailing junk or
        # truncation). With sudspy strict=False these are RECOVERED (not lost)
        # — the count surfaces how many files had the trailing-appendix issue.
        "n_recovered": qc.get("n_recovered", 0),
        "bogus_year_traces_dropped": len(bogus),
        "sds_files_written": [],
    }
    if commit and stream:
        written = _write_sds_retry(suds_convert, stream, staging_sds_root)
        result["sds_files_written"] = [(str(p), sz) for p, sz in written]
    elif stream:
        # Dry-run: just compute the output paths so the operator can review
        from collections import defaultdict
        groups = defaultdict(int)
        for tr in stream:
            p = suds_convert._sds_day_path(staging_sds_root, tr)
            groups[str(p)] += tr.stats.npts
        result["sds_files_planned"] = [(p, n) for p, n in sorted(groups.items())]
    return result


def _worker_convert_day(job):
    """Pickle-safe worker. Opens its own DB connection, imports suds_convert
    internally (so the parent doesn't have to pass the module across pickle).
    `job` is a plain dict so it pickles cleanly.
    """
    import sqlite3 as _sql
    sys.path.insert(0, job["disk_to_sds"])
    import suds_convert  # noqa: E402
    conn = _sql.connect(job["db_path"])
    try:
        sta = job["station"]
        net = job["network"]
        loc = job["location"]
        d_iso = job["date"]
        d = date.fromisoformat(d_iso)
        recorder_eff = job["recorder_eff"]
        staging = job["staging_sds"]
        commit = job["commit"]
        floor_ratio = job["disk_size_floor_ratio"]

        # Cross-source-aware file selection: pulls disk+tele candidates and
        # runs the per-HHMM selector in scan/cross_source.py. For clean disk
        # days (no tele competition) the output is identical to the legacy
        # query_day_files; for partial days the selector reclaims data from
        # telemetry where disk has gaps.
        files = query_cross_source_day_files(conn, sta, d, recorder_eff, floor_ratio)

        try:
            if recorder_eff == "echopro":
                r = convert_echopro_day(suds_convert, sta, net, loc, files, staging, commit)
            elif recorder_eff == "gecko":
                r = convert_gecko_day(sta, net, loc, files, staging, commit, suds_convert)
            elif recorder_eff == "minimus":
                r = convert_minimus_day(sta, net, loc, files, staging, commit, suds_convert)
            else:
                r = {"status": "unsupported_recorder", "n_files": len(files)}
        except Exception as e:
            r = {"status": "error", "n_files": len(files),
                 "error": f"{type(e).__name__}: {e}",
                 "traceback": traceback.format_exc()}
        r["date"] = d_iso
        return r
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="Level-1 manifest SQLite path")
    ap.add_argument("plan", help="per-station plan YAML from plan_generator.py")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--staging-sds", required=True,
                    help="staging SDS root, e.g. /mnt/seiscomp_staging/seiscomp_archive")
    ap.add_argument("--commit", action="store_true",
                    help="actually write SDS files (default: dry-run)")
    ap.add_argument("--limit-days", type=int, default=None,
                    help="stop after this many days converted (for smoke tests)")
    ap.add_argument("--start-date", help="restrict window (YYYY-MM-DD)")
    ap.add_argument("--end-date", help="restrict window (YYYY-MM-DD)")
    ap.add_argument("--dates-file", help="path to file with one YYYY-MM-DD per line; "
                                         "if set, ONLY those dates are converted "
                                         "(ignores --start-date/--end-date). Used for "
                                         "random stress sampling where the chosen dates "
                                         "are non-contiguous.")
    ap.add_argument("--disk-to-sds", default=SUDS_CONVERT_PATH_DEFAULT,
                    help="path containing suds_convert.py (disk_to_sds engine)")
    ap.add_argument("--workers", type=int, default=1,
                    help="per-day parallel workers (default 1 = serial). Each worker opens "
                         "its own SQLite connection and converts one day at a time. Tune to "
                         "find NFS-IOPS ceiling for your archive.")
    ap.add_argument("--disk-size-floor-ratio", type=float,
                    default=DEFAULT_DISK_SIZE_FLOOR_RATIO,
                    help="Cross-source tier-2 threshold. Disk wins iff "
                         "disk_size >= ratio * tele_size. Default 0.8 (disk wins "
                         "down to 80%% of tele's size). Set 0.0 for old 'absolute "
                         "disk preference' policy; 1.0 for 'most data wins, disk "
                         "only on ties'.")
    ap.add_argument("--run-manifest", default=None,
                    help="path to write a phase3 run manifest (JSON) at end of run. "
                         "When set, captures run identity, per-date status, "
                         "aggregate counts, and a policy_sha+policy_yaml_path that "
                         "sds_staging_ledger/apply.py can read via its own "
                         "--run-manifest flag to copy the plan into policies/ and "
                         "augment events.jsonl source dicts.")
    ap.add_argument("--classifier-version", default="v3-OptionB",
                    help="classifier version label embedded in the run manifest. "
                         "Defaults to the current Option-B classifier. Bump when "
                         "the classifier semantics change.")
    args = ap.parse_args()

    import yaml
    # Fail-fast: confirm suds_convert is importable from --disk-to-sds before
    # we spin up the pool. Each worker will re-import (cheap; Python caches).
    sys.path.insert(0, args.disk_to_sds)
    import suds_convert  # noqa: F401,E402  (probe import only)

    plan = yaml.safe_load(open(args.plan))
    station = plan["station"]
    network = plan["network"]
    location = plan.get("location", "00")
    status = plan["status"]

    print(f"[phase3] station={network}.{station} loc={location!r} status={status} "
          f"commit={args.commit} dry-run={not args.commit}", flush=True)

    # Run-manifest setup: capture identity at start; we'll fill in aggregates +
    # per-date status during the run and write the manifest at the end.
    # Manifest is a hand-off file for sds_staging_ledger/apply.py — see
    # CLAUDE.md "Ledger integration".
    run_manifest_state = None
    if args.run_manifest:
        plan_path_abs = os.path.abspath(args.plan)
        plan_bytes = open(plan_path_abs, "rb").read()
        plan_sha = hashlib.sha256(plan_bytes).hexdigest()
        # project_git: best-effort. None if not in a git checkout.
        _here = os.path.dirname(os.path.abspath(__file__))
        try:
            _gr = _sp.run(["git", "-C", _here, "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=5)
            project_git = _gr.stdout.strip() if _gr.returncode == 0 else None
        except Exception:
            project_git = None
        run_start_dt = datetime.now(timezone.utc)
        run_id = (f"eqserver_{network}_{station}_"
                  f"{run_start_dt.strftime('%Y%m%dT%H%M%SZ')}")
        run_manifest_state = {
            "started_at": run_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": run_id,
            "plan_path_abs": plan_path_abs,
            "plan_sha": plan_sha,
            "project_git": project_git,
            "phase3_argv": sys.argv,
        }
        print(f"[phase3] run-manifest target: {args.run_manifest}", flush=True)
        print(f"[phase3]   run_id={run_id}", flush=True)
        print(f"[phase3]   policy_sha={plan_sha}", flush=True)
        print(f"[phase3]   project_git={project_git or '(unavailable)'}", flush=True)

    if status == "defer_conversion":
        print(f"[phase3] defer_conversion: {plan.get('defer_reason','')}", flush=True)
        return 0
    # Every day in the epoch range is attempted. flagged_days is descriptive
    # QA metadata, NOT a skip signal. Days that can't be read return
    # status=no_files / status=parse_error from the worker — runtime
    # pathology, not classifier-driven pre-filter.
    n_flagged_skipped = 0  # kept at 0 for manifest schema stability

    # Recorder aliases: file layout / read path is identical, only the
    # registry label differs. RT130 + Gecko both produce dashed-date .ms.zip
    # with the same read pattern, so they share the gecko branch.
    RECORDER_ALIASES = {"reftek_rt130": "gecko"}
    SUPPORTED = {"echopro", "gecko", "minimus"}

    def effective(ep):
        r = ep.get("recorder")
        return RECORDER_ALIASES.get(r, r)

    epochs = plan.get("epochs", [])
    epochs_to_run = [ep for ep in epochs if effective(ep) in SUPPORTED]
    epochs_skipped = [ep for ep in epochs if effective(ep) not in SUPPORTED]
    for ep in epochs_skipped:
        print(f"[phase3] SKIP epoch {ep['id']} ({ep['start']}..{ep['end']}) "
              f"recorder={ep.get('recorder')} — supported: {sorted(SUPPORTED)}", flush=True)

    # Build the date list across all supported epochs, optionally clipped
    user_start = date.fromisoformat(args.start_date) if args.start_date else None
    user_end = date.fromisoformat(args.end_date) if args.end_date else None
    user_dates = None
    if args.dates_file:
        with open(args.dates_file) as f:
            user_dates = {ln.strip() for ln in f
                          if ln.strip() and not ln.startswith("#")}
        print(f"[phase3] --dates-file: {len(user_dates)} explicit dates",
              flush=True)

    jobs = []
    for ep in epochs_to_run:
        ep_start = date.fromisoformat(ep["start"])
        ep_end = date.fromisoformat(ep["end"])
        recorder_eff = effective(ep)
        for d in _iter_days(ep_start, ep_end):
            iso = d.isoformat()
            if user_dates is not None:
                if iso not in user_dates:
                    continue
            else:
                if user_start and d < user_start:
                    continue
                if user_end and d > user_end:
                    continue
            jobs.append({
                "station": station, "network": network, "location": location,
                "date": iso, "recorder_eff": recorder_eff,
                "db_path": args.db, "staging_sds": args.staging_sds,
                "commit": args.commit, "disk_to_sds": args.disk_to_sds,
                "disk_size_floor_ratio": args.disk_size_floor_ratio,
            })
            if args.limit_days and len(jobs) >= args.limit_days:
                break
        else:
            continue
        break

    print(f"[phase3] {len(jobs)} day-jobs queued, workers={args.workers}", flush=True)

    results = Counter()
    written_bytes = 0
    days_processed = 0
    # Per-date status records, populated when --run-manifest is set. Compact —
    # one dict per day-job. Lands in the manifest's eqserver.per_date_status.
    per_date_status: list = []
    t0 = time.time()

    def _emit(r):
        nonlocal written_bytes, days_processed
        days_processed += 1
        results[r["status"]] += 1
        day_bytes = sum(sz for _, sz in r.get("sds_files_written", []))
        written_bytes += day_bytes
        n_written = len(r.get("sds_files_written", []))
        n_planned = len(r.get("sds_files_planned", []))
        mark = "WRITE" if args.commit else "PLAN"
        iso = r.get("date", "?")
        if run_manifest_state is not None:
            per_date_status.append({
                "date": iso,
                "status": r["status"],
                "n_files": r.get("n_files", 0),
                "n_traces": r.get("n_traces", 0),
                "rate_hz": r.get("rate_hz"),
                "read_errors": r.get("read_errors", 0),
                "bytes_written": day_bytes,
            })
        if r["status"] == "error":
            print(f"  [{iso}] ERROR {r.get('error')}", flush=True)
            if "traceback" in r:
                print(r["traceback"], flush=True)
            return
        print(f"  [{iso}] {r['status']:10} files={r['n_files']:>5} "
              f"traces={r.get('n_traces',0):>3} "
              f"rate={r.get('rate_hz') or 0:>5} "
              f"errs={r.get('read_errors',0)} "
              f"recovered={r.get('n_recovered',0)} "
              f"dropped={r.get('dropped_components',[])} "
              f"bogus_yr={r.get('bogus_year_traces_dropped',0)} "
              f"{mark}={n_written + n_planned}", flush=True)

    if args.workers <= 1:
        # Serial path — keep for direct debugging and to isolate NFS effects.
        for job in jobs:
            _emit(_worker_convert_day(job))
    else:
        # Parallel path. imap_unordered streams results as workers finish,
        # so output appears continuously rather than in one final flush.
        from multiprocessing import Pool
        with Pool(processes=args.workers) as pool:
            for r in pool.imap_unordered(_worker_convert_day, jobs):
                _emit(r)

    elapsed = time.time() - t0
    rate = days_processed / elapsed if elapsed > 0 else 0
    print(f"\n[phase3] done in {elapsed:.1f}s ({rate:.2f} days/sec aggregate)")
    print(f"  status counts: {dict(results)}")
    print(f"  days processed: {days_processed}")
    if args.commit:
        print(f"  total bytes written: {written_bytes:,}")
    else:
        print(f"  (dry-run: pass --commit to actually write SDS)")

    # Run manifest: write the hand-off file for apply.py if --run-manifest set.
    # Written unconditionally (dry-run or --commit) so the schema can be
    # exercised end-to-end during staging-only stress rehearsals.
    if run_manifest_state is not None:
        n_ok = results.get("ok", 0)
        n_qc = results.get("qc_flagged", 0)
        n_err = results.get("error", 0)
        n_parse_err = results.get("parse_error", 0)
        n_no_files = results.get("no_files", 0)
        items_succeeded = n_ok + n_qc
        items_failed = n_err + n_parse_err
        finished_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest_doc = {
            "run_id": run_manifest_state["run_id"],
            "kind": "eqserver",
            "project": "eqserver_2_seiscomp",
            "project_git": run_manifest_state["project_git"],
            "host": socket.gethostname(),
            "operator": os.environ.get("USER", "unknown"),
            "started_at": run_manifest_state["started_at"],
            "finished_at": finished_iso,
            "net": network,
            "sta": station,
            "target_root": os.path.abspath(args.staging_sds),
            "policy_sha": run_manifest_state["plan_sha"],
            "policy_yaml_path": run_manifest_state["plan_path_abs"],  # transit-only
            "classifier_version": args.classifier_version,
            "aggregate": {
                "items_attempted": days_processed,
                "items_succeeded": items_succeeded,
                "items_failed": items_failed,
                "bytes_written": written_bytes,
            },
            "phase3_invocation": {
                "command": run_manifest_state["phase3_argv"],
                "argv": {
                    "workers": args.workers,
                    "commit": args.commit,
                    "limit_days": args.limit_days,
                    "start_date": args.start_date,
                    "end_date": args.end_date,
                    "dates_file": args.dates_file,
                    "disk_size_floor_ratio": args.disk_size_floor_ratio,
                },
            },
            "eqserver": {
                "per_date_status": per_date_status,
                "days_no_files": n_no_files,
                "flagged_days_skipped": n_flagged_skipped,
                "read_errors": sum(d.get("read_errors", 0) for d in per_date_status),
                "elapsed_s": round(elapsed, 1),
                "throughput_days_per_s": round(rate, 4),
            },
        }
        # Atomic write so a reader never sees a partial manifest.
        out_path = os.path.abspath(args.run_manifest)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tmp = out_path + ".partial"
        with open(tmp, "w") as f:
            json.dump(manifest_doc, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out_path)
        print(f"[phase3] run manifest written: {out_path}", flush=True)

    # Ledger handoff: after a successful --commit run, suggest the apply.py
    # command line that promotes this station's staging output to the LT
    # archive. We never invoke apply.py automatically — promotion is operator-
    # gated by design (always review the apply.py dry-run before --commit).
    if args.commit and days_processed > 0 and results.get("ok", 0) > 0:
        ledger_root = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive"
        apply_py = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/sds_staging_ledger/apply.py"
        lt_root = "/mnt/seiscomp_archive"
        print()
        print("[phase3] To promote this station's staging output to the LT archive:")
        if run_manifest_state is not None:
            print(f"  1. Dry-run to review (no writes), pinning provenance via run-manifest:")
            print(f"     python3 {apply_py} \\")
            print(f"         --staging-root {args.staging_sds} \\")
            print(f"         --lt-root {lt_root} \\")
            print(f"         --ledger-root {ledger_root} \\")
            print(f"         --net {network} --sta {station} \\")
            print(f"         --source-kind eqserver \\")
            print(f"         --run-manifest {os.path.abspath(args.run_manifest)} \\")
            print(f"         --mode decide")
            print(f"  2. Then add --commit when the dry-run looks right.")
        else:
            source_card = f"eqserver_{network}_{station}_{date.today().isoformat()}"
            print(f"  1. Dry-run to review (no writes):")
            print(f"     python3 {apply_py} \\")
            print(f"         --staging-root {args.staging_sds} \\")
            print(f"         --lt-root {lt_root} \\")
            print(f"         --ledger-root {ledger_root} \\")
            print(f"         --net {network} --sta {station} \\")
            print(f"         --source-kind eqserver --source-card {source_card} \\")
            print(f"         --mode decide")
            print(f"  2. Then add --commit when the dry-run looks right.")
            print(f"  NOTE: this run did not produce a run-manifest. Re-running phase3 "
                  f"with --run-manifest /tmp/run.json gives apply.py full provenance "
                  f"(policy_sha + run_id + project_git in every events.jsonl line).")


if __name__ == "__main__":
    sys.exit(main())

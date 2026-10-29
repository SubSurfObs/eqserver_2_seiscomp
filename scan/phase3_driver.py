#!/usr/bin/env python3
"""Phase 3 per-station EchoPro driver.

Consumes a station plan YAML (from plan_generator.py) plus the Level-1 manifest,
dispatches each "clean" day through disk_to_sds/scripts/suds_convert.py, and
writes the result to a staging SDS root.

Requires --commit to actually do anything. Without --commit, phase3 still
reads + parses + merges files (useful for surfacing parse errors / file-level
problems) but does not write SDS. Earlier versions of this file also computed
a "would-write" SDS path list in dry-run mode via the engine's private
_sds_day_path helper; that path was removed when disk_to_sds's write_sds
refactor (B / 00b6835) dropped the helper.

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

_ENGINE_SHA = "?"  # set in main(); carried into jobs for day fingerprints

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cross_source import select_files_for_day, DEFAULT_DISK_SIZE_FLOOR_RATIO  # noqa: E402

# disk_to_sds engine on the VM. Path is resolved at import time so the user
# can override via PYTHONPATH or the --disk-to-sds flag.
SUDS_CONVERT_PATH_DEFAULT = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/scripts"


DEFAULT_MIN_DATA_YEAR = 2012   # UoM VW/VX/DU operations didn't start before this
                                # — anything before is no-GPS-lock or bogus header


def _seed_band(rate_hz: float) -> str:
    """SEED band code by sample rate (Gecko convention, broadband side)."""
    r = float(rate_hz or 0)
    if r >= 1000: return "F"
    if r >= 250:  return "C"
    if r >= 80:   return "H"
    if r >= 10:   return "B"
    if r >= 1:    return "L"
    if r >= 0.1:  return "V"
    return "U"


def _enforce_band_instrument(stream):
    """Rewrite each trace's channel code to `band_by_rate + 'H' + orientation`.

    The orientation (3rd letter) and station+network are preserved; only the
    band code (1st letter) and instrument code (2nd letter) are normalised.
    NOOP for traces whose source channel already conforms (e.g. Gecko 'CHZ' at
    250 Hz, RT130 borehole 'HH1' at 200 Hz). Required for the mixed-separator
    cohort at WPSH where the mseed headers carry Kelunji-native 'DL?' codes
    (D=datalogger-numeric, L=Lowgain) — without this rewrite the staging SDS
    files come out as DLE/DLN/DLZ instead of CHE/CHN/CHZ.

    Instrument letter is hard-set to 'H' (velocity seismometer); accelerometers
    are dropped upstream via channel_exclude, so we never see 'N' here.

    Idempotent and safe to apply on every conversion.
    """
    for tr in stream:
        ch = getattr(tr.stats, "channel", "") or ""
        if len(ch) != 3:
            continue
        orient = ch[-1]
        new_ch = _seed_band(tr.stats.sampling_rate) + "H" + orient
        if new_ch != ch:
            tr.stats.channel = new_ch
    return stream


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


def _trim_to_day(stream, d):
    """Trim stream to [d, d+1) so this day-job writes ONLY day d's SDS file.

    Half of the midnight-boundary fix (Option C in CLAUDE.md). Day-job N pulls
    day N-1's 2359 file in as a boundary tail (to capture day N's first SS
    seconds). After merge, this trim drops anything outside day N's wall-clock
    range. Net effect:

      - Day-job N captures + writes its OWN boundary sliver (the first SS s).
      - Day-job N does NOT write to day N-1's SDS file (so it can't race with
        day-job N-1, which would otherwise overwrite each other under
        parallel workers).
      - The tail of day N that spills into day N+1 (from day N's own 2359
        file) is dropped here but recovered by day-job N+1, which pulls
        day N's 2359 in as ITS boundary tail.

    Uses a 1-microsecond epsilon at endtime so a sample at exactly
    day_(N+1)_start (which belongs to day N+1) is excluded.
    """
    if stream is None or len(stream) == 0 or d is None:
        return stream
    from obspy import UTCDateTime
    day_start = UTCDateTime(d.isoformat() + "T00:00:00")
    day_end = day_start + 86400 - 1e-6
    return stream.trim(starttime=day_start, endtime=day_end,
                       nearest_sample=False)


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


def _concat_zip_members(files, combined, read_errors, spans=None):
    """Read each .ms.zip's inner mseed and append its bytes to `combined`.

    Reads the whole .ms.zip in ONE sequential pass, then opens the ZIP from
    memory. The ZIP format needs random-access seeks (End-Of-Central-Directory
    at the tail, then back to the member); on an NFS file handle each seek is a
    round-trip. Reading the (tiny, ~12-70 KB) whole file once collapses that to
    a single round-trip — ~2x faster at workers=1 on cold NFS, which matters
    most for the Gecko/Minimus cohorts (the slow ones). Used by both the gecko
    and minimus branches so the I/O pattern is identical.

    When `spans` is a list, appends one (path, start, end) tuple per file
    marking its byte range inside `combined`, so the corrupt-record fallback
    can re-parse per file FROM MEMORY instead of re-reading NFS.
    """
    import io as _io
    import zipfile as _zip
    for path in files:
        start = combined.tell()
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
        if spans is not None:
            spans.append((path, start, combined.tell()))



def _mseed_record_scan(buf: bytes):
    """Walk fixed miniSEED headers in a concatenated buffer.

    Yields (offset, reclen, id_key, stated_start_epoch, npts, rate).
    Pure header arithmetic — no decoding. Returns None (not raises) on any
    shape this parser doesn't understand; the caller then degrades to a
    QC-FLAGGED per-file read, never a silent whole-buffer read (review
    2026-10-23, F6).

    Per review F6 this scan handles, per record (not per buffer): byte
    order (year plausibility), record length (each record's own blockette
    1000), the fixed-header time correction (0.0001 s units, applied only
    when activity-flag bit 0x02 says it has not been applied yet) and the
    blockette-1001 microsecond refinement — matching the effective start
    times ObsPy/libmseed computes, so split decisions and the final parse
    agree. Records with no waveform samples (log/admin, npts or rate 0)
    are stepped over, not treated as scan failure.
    """
    import struct
    out = []
    pos, n = 0, len(buf)
    while pos + 48 <= n:
        rec = buf[pos:pos + 48]
        if not (rec[0:6].isdigit() and rec[6:7] in b"DRQM"):
            return None
        order = None
        for bo in (">", "<"):
            y = struct.unpack(bo + "H", rec[20:22])[0]
            j = struct.unpack(bo + "H", rec[22:24])[0]
            if 1950 <= y <= 2100 and 1 <= j <= 366:
                order, year, jday = bo, y, j
                break
        if order is None:
            return None
        hh, mm, ss = rec[24], rec[25], rec[26]
        t0001 = struct.unpack(order + "H", rec[28:30])[0]
        npts = struct.unpack(order + "H", rec[30:32])[0]
        rf = struct.unpack(order + "h", rec[32:34])[0]
        rm = struct.unpack(order + "h", rec[34:36])[0]
        act_flags = rec[36]
        tcorr = struct.unpack(order + "i", rec[40:44])[0]
        # Blockette chain for THIS record: 1000 (record length, required)
        # and 1001 (microsecond refinement, optional).
        reclen = None
        usec1001 = 0
        cur = struct.unpack(order + "H", rec[46:48])[0]
        hops = 0
        while 48 <= cur <= 4096 and hops < 8 and pos + cur + 8 <= n:
            btype = struct.unpack(order + "H", buf[pos + cur:pos + cur + 2])[0]
            bnext = struct.unpack(order + "H", buf[pos + cur + 2:pos + cur + 4])[0]
            if btype == 1000:
                reclen = 2 ** buf[pos + cur + 6]
            elif btype == 1001:
                usec1001 = struct.unpack("b", buf[pos + cur + 5:pos + cur + 6])[0]
            if bnext <= cur:
                break
            cur = bnext
            hops += 1
        if reclen is None or reclen < 64 or pos + reclen > n:
            return None
        if npts == 0 or rf == 0 or rm == 0:
            pos += reclen            # log/admin record: no waveform time
            continue
        if rf > 0 and rm > 0: rate = rf * rm
        elif rf > 0 and rm < 0: rate = -rf / rm
        elif rf < 0 and rm > 0: rate = -rm / rf
        else: rate = 1.0 / (rf * rm)
        sta = rec[8:13].decode("ascii", "replace").strip()
        loc = rec[13:15].decode("ascii", "replace").strip()
        cha = rec[15:18].decode("ascii", "replace").strip()
        net = rec[18:20].decode("ascii", "replace").strip()
        from datetime import datetime, timezone, timedelta
        usec = t0001 * 100 + usec1001
        if not (act_flags & 0x02):   # time correction not yet applied
            usec += tcorr * 100
        try:
            t = (datetime(year, 1, 1, tzinfo=timezone.utc)
                 + timedelta(days=jday - 1, hours=hh, minutes=mm,
                             seconds=ss, microseconds=usec)).timestamp()
        except (ValueError, OverflowError):
            return None
        out.append((pos, reclen, f"{net}.{sta}.{loc}.{cha}", t, npts, rate))
        pos += reclen
    return out if pos == n else None


def _drift_split_segments(buf: bytes):
    """Returns (status, segments): status is "clean" (grid-consistent, the
    plain bulk read is safe), "split" (segments holds drift-consistent byte
    runs per trace-id), or "scan_failed" (buffer not parseable by the header
    scan — the caller must use a timestamp-preserving per-file read, NEVER
    the silent whole-buffer weld; review 2026-10-23 F6).

    This is the timestamp-fidelity guard at the READER level: obspy's
    record joining steps record-by-record, so recorder clock drift
    (DU.LKHRT ~10 ms/hour) accumulates into silently shifted sample
    times. Convert the data as it is timestamped (operator 2026-10-22).
    """
    recs = _mseed_record_scan(buf)
    if recs is None:
        return "scan_failed", None
    if not recs:
        return "clean", None
    by_id = {}
    for off, reclen, key, t, npts, rate in recs:
        by_id.setdefault(key, []).append((off, reclen, t, npts, rate))
    segments = {}
    any_split = False
    for key, rows in by_id.items():
        segs, cur = [], []
        anchor_t = grid_n = None
        for off, reclen, t, npts, rate in rows:
            if rate <= 0:
                return "scan_failed", None
            dt = 1.0 / rate
            if cur and abs((t - anchor_t) - grid_n * dt) > 0.5 * dt:
                segs.append(cur)
                cur = []
            if not cur:
                anchor_t, grid_n = t, 0
            cur.append((off, reclen))
            grid_n += npts
        if cur:
            segs.append(cur)
        if len(segs) > 1:
            any_split = True
        segments[key] = segs
    if not any_split:
        return "clean", None
    return "split", segments


def _parse_combined_with_fallback(all_files, read_errors):
    """Concat + bulk-parse the day's mseed; on a corrupt record, fall back to
    per-file parsing of the SAME in-memory bytes (no second NFS pass).

    Patch 2026-10-17 (Project-1 minor patch, from the QA campaign /
    efficiency review F4): the previous fallback re-read every .ms.zip from
    NFS — a full extra read phase (~40-170 s cold) whenever one record was
    corrupt. It also parsed ALL zip members where the bulk path uses only
    the first data member per zip; the fallback now parses exactly the
    bytes the bulk path saw, so good-day and fallback-day semantics match.

    Returns (stream, bulk_fallback_used, bulk_fallback_reason).
    """
    import io
    from obspy import read, Stream
    combined = io.BytesIO()
    spans = []
    _concat_zip_members(all_files, combined, read_errors, spans=spans)
    buf = combined.getvalue()
    # Timestamp-fidelity pre-scan: if any id's records drift >0.5 sample
    # from their segment grid, parse drift-consistent byte segments
    # separately so stated times are preserved (otherwise obspy's record
    # joining welds them). "clean" => single fast read as before.
    # "scan_failed" => the buffer's shape defeated the header scan; degrade
    # to a PER-FILE read (timestamp drift within one minute-file is
    # negligible) and QC-flag the day — never the silent whole-buffer weld
    # (review 2026-10-23, F6).
    try:
        scan_status, segs = _drift_split_segments(buf)
    except Exception as se:
        scan_status, segs = "scan_failed", None
        read_errors.append(("record-scan", f"{type(se).__name__}: {se}"))
    def _per_file_read(reason):
        # Timestamp-preserving degradation, always QC-flagged: parse each
        # file's own bytes from the retained buffer. Never the whole-buffer
        # weld (Codex fix-review F6 closed the split-failure fall-through).
        st = Stream()
        bb = combined.getbuffer()
        for path, a, b in spans:
            if b <= a:
                continue  # file already recorded as a read error
            try:
                st += read(io.BytesIO(bytes(bb[a:b])), format="MSEED")
            except Exception as fe:
                read_errors.append(
                    (path, f"per-file(mem) {type(fe).__name__}: {fe}"))
        return st, True, reason

    if scan_status == "split":
        from obspy import Stream as _Stream
        st = _Stream()
        seg_fail = False
        for key, seglist in segs.items():
            for seg in seglist:
                part = b"".join(buf[o:o + L] for o, L in seg)
                try:
                    st += read(io.BytesIO(part), format="MSEED")
                except Exception as fe:
                    seg_fail = True
                    read_errors.append((key, f"segment: {type(fe).__name__}: {fe}"))
        if len(st) > 0:
            return st, seg_fail, ("some drift segments unparseable; "
                                  "parsed remainder" if seg_fail else None)
        return _per_file_read("all drift segments unparseable; per-file read")
    elif scan_status == "scan_failed":
        return _per_file_read("record-scan unparseable; per-file read "
                              "(timestamp-preserving degradation)")
    combined.seek(0)
    try:
        return read(combined, format="MSEED"), False, None
    except Exception as e:
        # Bulk-read failed — typically one corrupted mseed record (Steim
        # integrity check failure) somewhere in the concatenated buffer.
        # Per-file fallback from the retained buffer drops only the files
        # that fail. Triggered ~1/100 days on gecko stations with on-disk
        # corruption (BRTH 2021-01-08 etc).
        reason = f"bulk: {type(e).__name__}: {e}"
        st = Stream()
        buf = combined.getbuffer()
        for path, a, b in spans:
            if b <= a:
                continue  # file already recorded as a read error
            try:
                st += read(io.BytesIO(bytes(buf[a:b])), format="MSEED")
            except Exception as fe:
                read_errors.append(
                    (path, f"per-file(mem) {type(fe).__name__}: {fe}"))
        return st, True, reason


def _start_phase3_watchdog(deadline_s=10800):
    """Force-kill the phase3 process if it runs longer than `deadline_s` wall-clock.

    This is the safety net for hangs the per-day-job SIGALRM (in
    _worker_convert_day) does not catch — specifically, the
    multiprocessing.Pool context-manager teardown hanging after all
    day-jobs have returned their results. Observed pattern: all 365
    day-lines emitted to the log, then phase3 sits forever in
    `pool.__exit__()` waiting for a worker process that won't die.

    LOYU 2016 hit this on 2026-06-06 (3.4h stuck before manual kill).
    DDWB 2022 hit a milder version on 2026-06-04 (~30 min before
    self-resolving). The SIGALRM in _worker_convert_day doesn't help
    because by the time the hang occurs, all workers' SIGALRM handlers
    have already been cleared (alarm(0) in the finally).

    Default deadline 10800s = 3 hours. The slowest legitimate unit
    observed (DDWB 2018 with workers=4) was 8783s = 2.4h. With workers=8
    the slowest are ~1.5h. 3h gives a comfortable margin over legitimate
    completions while bounding hangs at +1h instead of "indefinite".

    Uses `os.kill(os.getpid(), SIGKILL)` rather than SIGTERM because the
    hang we're protecting against is in C code that doesn't respond to
    signal handlers. SIGKILL can't be blocked — the kernel reaps the
    process. convert.py then records `FAIL <STA> <YEAR> rc=-9` and
    advances to the next unit.
    """
    import threading
    import signal as _signal
    start = time.time()

    def _bark():
        deadline = start + deadline_s
        while time.time() < deadline:
            time.sleep(60)
        # We are past the deadline. Force-kill.
        print(f"\n[phase3] WATCHDOG: exceeded {deadline_s}s wall-clock without "
              f"clean exit; force-killing PID {os.getpid()} with SIGKILL",
              file=sys.stderr, flush=True)
        # IMPORTANT: kill ONLY this phase3 process, NOT the process group.
        # phase3 is spawned via subprocess.run() from convert.py, so it
        # inherits convert.py's pgid. An os.killpg(os.getpgrp(), SIGKILL)
        # would kill the orchestrator AND its tmux session — exactly what
        # happened to LRNW 2020 on 2026-06-07, halting the entire sweep.
        # Multiprocessing.Pool workers will be orphaned briefly, then reaped
        # by the kernel; that's fine, they don't affect convert.py's view.
        os.kill(os.getpid(), _signal.SIGKILL)

    t = threading.Thread(target=_bark, name="phase3_watchdog", daemon=True)
    t.start()
    return t


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


def query_boundary_tail_files(conn, station, d, recorder, disk_size_floor_ratio):
    """Return previous-day (d-1) HHMM='2359' files — the midnight-boundary
    tail that carries data extending into day d.

    EqServer minute-files are filed by their filename HHMM start time, but each
    file's data extends SS seconds into the next day (constant SS within a
    recording session). The 23:59 file of day d-1 thus contains the first SS
    seconds of day d. Without including it, day d's SDS starts at 00:00:SS
    instead of 00:00:00 — ~12 s lost per channel per day on a typical recorder.
    See "Midnight-boundary data loss" in CLAUDE.md.

    Same cross-source dedup as query_cross_source_day_files but restricted to
    HHMM='2359'. Empty list if d-1 has no data, no 2359 file, or is in a
    different recorder epoch (year transitions handled naturally — the manifest
    is indexed by dir_year/month/day so December 31 of the prior year resolves
    fine).
    """
    from datetime import timedelta as _td
    d_prev = d - _td(days=1)
    if recorder == "minimus":
        sql = (
            "SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
            "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
            "  AND recorder_type = 'mseed' AND exclude_reason = 'single_channel' "
            "  AND hhmm = '2359'"
        )
        params = (station, d_prev.year, d_prev.month, d_prev.day)
    else:
        sql = (
            "SELECT path, source_type, hhmm, channel_suffix, size_bytes FROM files "
            "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
            "  AND recorder_type = ? AND exclude_reason IS NULL "
            "  AND hhmm = '2359'"
        )
        params = (station, d_prev.year, d_prev.month, d_prev.day, recorder)
    rows = conn.execute(sql, params).fetchall()
    return select_files_for_day(rows, disk_size_floor_ratio=disk_size_floor_ratio)


def convert_gecko_day(station, network, location, files, staging_sds_root, commit, suds_convert,
                      boundary_files=None, day=None, orient_filter=None,
                      resolve_mode=False):
    """Gecko branch: read all .ms.zip in day, concat the inner mseed records into
    one buffer, parse with ObsPy, override network+location, merge, write SDS.

    Gecko's mseed headers already carry correct channel (CHZ/CHN/CHE) and station;
    only network (UM placeholder) and location (empty '') need patching. The
    location value comes from the plan YAML (registry-driven; default "00").
    STEIM2 encoding is preserved end-to-end (no decode-recode).

    When `boundary_files` + `day` are passed (the production path from
    `_worker_convert_day`), the boundary tail file(s) from day-1 are read with
    the day's files and the merged stream is trimmed to [day, day+1) so only
    day's SDS file is written. See Option C in CLAUDE.md / `_trim_to_day`.
    """
    boundary_files = boundary_files or []
    all_files = list(files) + list(boundary_files)
    if not all_files:
        return {"status": "no_files", "n_files": 0, "n_boundary_files": 0}
    read_errors = []
    st, bulk_fallback_used, bulk_fallback_reason = \
        _parse_combined_with_fallback(all_files, read_errors)
    if bulk_fallback_used and len(st) == 0:
        return {"status": "parse_error", "n_files": len(files),
                "n_boundary_files": len(boundary_files),
                "error": f"{bulk_fallback_reason}; per-file fallback yielded 0 traces",
                "read_errors": len(read_errors)}
    st = _apply_orient_filter(st, orient_filter)
    for tr in st:
        tr.stats.network = network
        tr.stats.location = location
    # Normalise channel codes: band by sample rate, instrument = H. NOOP for
    # Gecko-conforming inputs; required for the mixed-separator cohort whose
    # source mseed carries Kelunji-native 'DL?' codes.
    st = _enforce_band_instrument(st)
    # Replaces obspy Stream.merge(method=1, fill_value=None) + split() with
    # sudspy.fast_merge_split — O(N log N) vs O(N^2). 492x speedup measured
    # on HOLS 2018-06-15 (1440-file SUDS day), byte-equal output. Preserves
    # int dtype throughout (no float32 round-trip). See sudspy handoff
    # 2026-06-22 + commits 81dbd1a / 20c48b4.
    import sudspy as _sudspy
    st = _sudspy.fast_merge_split(st)
    st.sort(["starttime"])
    # Drop traces with bogus pre-2012 starttimes (no-GPS-lock guard).
    bogus = []
    st = _filter_bogus_year_traces(st, DEFAULT_MIN_DATA_YEAR, bogus)
    # Midnight-boundary fix (Option C): trim to day's wall-clock range so this
    # day-job writes ONLY day's SDS file. Without this, the boundary tail file
    # we pulled in from day-1 would also write into day-1's SDS file, racing
    # with day-job N-1 under parallel workers.
    st = _trim_to_day(st, day)

    rate = st[0].stats.sampling_rate if len(st) > 0 else None
    result = {
        "status": "ok" if (not read_errors and not bulk_fallback_used) else "qc_flagged",
        "n_files": len(files),
        "n_boundary_files": len(boundary_files),
        "n_traces": len(st),
        "rate_hz": rate,
        "dropped_components": [],
        "read_errors": len(read_errors),
        "bogus_year_traces_dropped": len(bogus),
        "bulk_fallback_used": bulk_fallback_used,
        "sds_files_written": [],
    }
    if bulk_fallback_reason:
        result["error"] = bulk_fallback_reason
    if resolve_mode:
        result["stream"] = st
        return result
    if commit and len(st) > 0:
        written = _write_sds_retry(suds_convert, st, staging_sds_root)
        result["sds_files_written"] = [(str(p), sz) for p, sz in written]
    return result


def _day_fingerprint(sel_rows, code_id, net, sta, loc, src_ids=None):
    """Identity of a day's conversion inputs (review 2026-10-23 F7): the
    selected (path, chans) set, the full selection artifact digest (which
    itself binds the manifest identity, so an index rebuild invalidates),
    the SHAs of ALL THREE executing code bases (this repo, disk_to_sds,
    sudspy) and the target identity. Matching fingerprint + intact outputs
    => the day's conversion is already done (skip). A fix in ANY code
    layer changes the fingerprint and forces reconversion."""
    import hashlib
    h = hashlib.sha256()
    h.update(f"{net}.{sta}.{loc}|{code_id}".encode())
    for row in sorted(sel_rows):
        sid = (src_ids or {}).get(row[0], "")
        h.update(f"|{row[0]}|{row[1]}|{row[2] if len(row) > 2 else ''}"
                 f"|{sid}".encode())
    return h.hexdigest()


def _file_sha256(path, _bufsize=1 << 20):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(_bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _skip_ledger_path(staging_root):
    from pathlib import Path as _P
    return _P(staging_root) / ".converted_days.jsonl"


def _load_skip_ledger(staging_root):
    import json as _j
    out = {}
    p = _skip_ledger_path(staging_root)
    if p.exists():
        for line in open(p):
            try:
                r = _j.loads(line)
                out[r["date"]] = r
            except Exception:
                continue
    return out


def _outputs_intact(rec):
    """Outputs must match the recorded size AND content hash (review
    2026-10-23 F7: size-only let same-size corruption skip). Ledger
    entries written before hashes were recorded never qualify."""
    from pathlib import Path as _P
    for entry in rec.get("outputs", []):
        if len(entry) < 3:
            return False  # pre-F7 ledger line: no hash recorded
        path, size, sha = entry[0], entry[1], entry[2]
        q = _P(path)
        try:
            if q.stat().st_size != size or _file_sha256(q) != sha:
                return False
        except OSError:
            return False
    return True


def _apply_orient_filter(st, orient_filter):
    """Keep only traces whose orientation letter is in orient_filter.
    Used by the v3 per-channel selection (chans groups): within one engine
    call all files share the same allowed set, so a stream-level filter is
    exactly per-file channel restriction (build-order Step 5)."""
    if not orient_filter:
        return st
    from obspy import Stream
    return Stream([tr for tr in st
                   if (tr.stats.channel or "?")[-1:].upper() in orient_filter])


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


def convert_minimus_day(station, network, location, files, staging_sds_root, commit, suds_convert,
                        boundary_files=None, day=None, orient_filter=None,
                        resolve_mode=False):
    """Minimus branch: per-channel-per-minute mseed zips (~4320 files/day).
    Each zip holds one minute of one component. Same in-memory concat-then-parse
    pattern as Gecko; ObsPy groups by trace id automatically. Location override
    comes from the plan YAML (registry-driven; default "00").

    Midnight-boundary handling identical to convert_gecko_day — see Option C
    in CLAUDE.md.
    """
    boundary_files = boundary_files or []
    all_files = list(files) + list(boundary_files)
    if not all_files:
        return {"status": "no_files", "n_files": 0, "n_boundary_files": 0}
    read_errors = []
    st, bulk_fallback_used, bulk_fallback_reason = \
        _parse_combined_with_fallback(all_files, read_errors)
    if bulk_fallback_used and len(st) == 0:
        return {"status": "parse_error", "n_files": len(files),
                "n_boundary_files": len(boundary_files),
                "error": f"{bulk_fallback_reason}; per-file fallback yielded 0 traces",
                "read_errors": len(read_errors)}
    st = _apply_orient_filter(st, orient_filter)
    for tr in st:
        tr.stats.network = network
        tr.stats.location = location
    # Normalise channel codes: band by sample rate, instrument = H. NOOP for
    # Minimus per-channel inputs already at HH? at 200 Hz.
    st = _enforce_band_instrument(st)
    # sudspy.fast_merge_split replaces stream.merge + split (492x speedup, see
    # convert_gecko_day for full rationale).
    import sudspy as _sudspy
    st = _sudspy.fast_merge_split(st)
    st.sort(["starttime"])
    # Drop traces with bogus pre-2012 starttimes (no-GPS-lock guard).
    bogus = []
    st = _filter_bogus_year_traces(st, DEFAULT_MIN_DATA_YEAR, bogus)
    # Midnight-boundary fix (Option C): trim to day's wall-clock range.
    st = _trim_to_day(st, day)

    rate = st[0].stats.sampling_rate if len(st) > 0 else None
    result = {
        "status": "ok" if (not read_errors and not bulk_fallback_used) else "qc_flagged",
        "n_files": len(files),
        "n_boundary_files": len(boundary_files),
        "n_traces": len(st),
        "rate_hz": rate,
        "dropped_components": [],
        "read_errors": len(read_errors),
        "bogus_year_traces_dropped": len(bogus),
        "bulk_fallback_used": bulk_fallback_used,
        "sds_files_written": [],
    }
    if bulk_fallback_reason:
        result["error"] = bulk_fallback_reason
    if resolve_mode:
        result["stream"] = st
        return result
    if commit and len(st) > 0:
        written = _write_sds_retry(suds_convert, st, staging_sds_root)
        result["sds_files_written"] = [(str(p), sz) for p, sz in written]
    return result


def convert_echopro_day(suds_convert, station, network, location, files, staging_sds_root, commit,
                        boundary_files=None, day=None, orient_filter=None,
                        resolve_mode=False):
    """One EchoPro station-day. Returns dict with n_files, n_traces, write_results, qc.

    suds_convert.convert_suds_files() resolves location via FDSN inventory if
    one is passed (inv=...); without an inventory it falls back to its own '00'
    default. We then enforce the registry-driven `location` on every trace
    afterwards so the plan YAML is the single source of truth for the SDS
    location code regardless of what the SUDS source said.

    Midnight-boundary handling identical to the gecko branch — see Option C
    in CLAUDE.md. The boundary tail file(s) from day-1 are fed into
    `convert_suds_files` alongside day's files; after the SUDS read we trim
    the stream to [day, day+1) so we write only day's SDS file.
    """
    boundary_files = boundary_files or []
    all_files = list(files) + list(boundary_files)
    if not all_files:
        return {"status": "no_files", "n_files": 0, "n_boundary_files": 0}
    stream, qc = suds_convert.convert_suds_files(all_files, network=network, station=station)
    stream = _apply_orient_filter(stream, orient_filter)
    for tr in stream:
        tr.stats.location = location
    # Drop traces with bogus pre-2012 starttimes (no-GPS-lock guard).
    bogus = []
    stream = _filter_bogus_year_traces(stream, DEFAULT_MIN_DATA_YEAR, bogus)
    # Midnight-boundary fix (Option C): trim to day's wall-clock range.
    stream = _trim_to_day(stream, day)
    result = {
        "status": "ok" if not qc["read_errors"] else "qc_flagged",
        "n_files": len(files),
        "n_boundary_files": len(boundary_files),
        # After trim, the trace count may differ from qc["n_traces"] (boundary
        # file's pre-midnight portion gets dropped). Report the post-trim count.
        "n_traces": len(stream),
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
    if resolve_mode:
        result["stream"] = stream
        return result
    if commit and stream:
        written = _write_sds_retry(suds_convert, stream, staging_sds_root)
        result["sds_files_written"] = [(str(p), sz) for p, sz in written]
    return result


def _write_day_replace(suds_convert, stream, sds_root, net, sta, day,
                       retries=3):
    """Clean-room day writer (Codex resolver review F7): the full day is
    materialized in a temporary directory on the same filesystem, then the
    target's inventory for this (station, day) is RECONCILED — expected
    files replace atomically, stale channel day-files from earlier runs
    are quarantined (never left to pose as current output). Existing bytes
    never participate in precedence. disk_to_sds's merge-on-write writer
    is deliberately untouched (SD-card workflow)."""
    import os as _os
    import time as _time
    import shutil as _sh
    import numpy as _np
    from pathlib import Path as _P
    from obspy import Stream as _Stream
    from obspy.io.mseed import InternalMSEEDError as _IME

    groups = {}
    for tr in stream:
        st_ = tr.stats
        for year, julday, piece in suds_convert._split_trace_by_utc_day(tr):
            path = suds_convert._sds_day_path_for(
                sds_root, st_.network, st_.station, st_.location,
                st_.channel, year, julday)
            groups.setdefault(_P(path), _Stream()).append(piece)

    jday = int(day.strftime("%j"))
    build = (_P(sds_root)
             / f".day_build.{net}.{sta}.{day.year}.{jday:03d}.{_os.getpid()}")
    build.mkdir(parents=True, exist_ok=True)
    staged = {}
    try:
        for path, st_ in groups.items():
            for tr in st_:
                if tr.data.dtype != _np.int32:
                    tr.data = tr.data.astype("int32")
            tmp = build / path.name
            encoding = "STEIM2"
            attempt = 0
            while True:
                try:
                    st_.write(str(tmp), format="MSEED",
                              encoding=encoding, reclen=512)
                    break
                except _IME:
                    if encoding != "STEIM2":
                        raise
                    # Glitch sample: STEIM2 cannot encode a >30-bit delta
                    # (disk_to_sds 88323ec). Keep the day in INT32.
                    encoding = "INT32"
                except OSError:
                    attempt += 1
                    if attempt >= retries:
                        raise
                    _time.sleep(2.0 * attempt)
            staged[path] = tmp

        # Reconcile: anything on target for this station+day that the
        # resolved output does not contain is STALE — quarantine it.
        sta_root = _P(sds_root) / str(day.year) / net / sta
        stale = []
        if sta_root.is_dir():
            pat = f"{net}.{sta}.*.{day.year}.{jday:03d}"
            for q in sta_root.glob(f"*/{pat}"):
                if q not in staged:
                    stale.append(q)
        if stale:
            qdir = (_P(sds_root) / ".stale_quarantine"
                    / f"{net}.{sta}.{day.year}.{jday:03d}."
                    f"{int(_time.time())}")
            qdir.mkdir(parents=True, exist_ok=True)
            for q in stale:
                _sh.move(str(q), str(qdir / q.name))

        written = []
        for path, tmp in staged.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            _os.replace(tmp, path)
            written.append((path, path.stat().st_size))
        return written
    finally:
        _sh.rmtree(build, ignore_errors=True)


def _clip_trace_ns(tr, ws_ns, we_ns):
    """Clip one obspy trace to the authorized window [ws, we) in exact
    integer nanoseconds. Returns (t0_ns, data_slice) or None."""
    s0 = int(tr.stats.starttime.ns)
    rate = round(float(tr.stats.sampling_rate))
    if rate <= 0 or 1_000_000_000 % rate:
        return None
    dt = 1_000_000_000 // rate
    n = len(tr.data)
    i0 = max(0, -((s0 - ws_ns) // dt))
    i1 = min(n, -((s0 - we_ns) // dt))
    if i1 <= i0:
        return None
    return (s0 + i0 * dt, tr.data[i0:i1])


def _read_file_traces(path, source_format, net, sta, loc, suds_convert,
                      read_errors, chan_map=None):
    """Per-file adapter (Codex resolver review F1/F4): read ONE source
    file, relabel to target identity, return its traces. No cross-file
    and no cross-format merging — a file's internal drift is split (a
    bundle is one file); everything else is the resolver's job."""
    from obspy import Stream
    if source_format == "suds":
        stream, qc = suds_convert.convert_suds_files(
            [path], network=net, station=sta)
        for e in qc.get("read_errors", []):
            read_errors.append((path, str(e)))
        st = stream
    else:
        import io
        from obspy import read
        buf = io.BytesIO()
        spans = []
        _concat_zip_members([path], buf, read_errors, spans=spans)
        b = buf.getvalue()
        st = Stream()
        if not b:
            return st
        try:
            scan_status, segs = _drift_split_segments(b)
        except Exception:
            scan_status, segs = "scan_failed", None
        if scan_status == "split":
            for key, seglist in segs.items():
                for seg in seglist:
                    part = b"".join(b[o:o + L] for o, L in seg)
                    try:
                        st += read(io.BytesIO(part), format="MSEED")
                    except Exception as fe:
                        read_errors.append(
                            (path, f"segment: {type(fe).__name__}: {fe}"))
        else:
            try:
                st += read(io.BytesIO(b), format="MSEED")
            except Exception as fe:
                read_errors.append((path, f"{type(fe).__name__}: {fe}"))
                return Stream()
            if scan_status == "scan_failed":
                read_errors.append(
                    (path, "record-scan unparseable (single-file read)"))
        for tr in st:
            tr.stats.network = net
        st = _enforce_band_instrument(st)
    for tr in st:
        tr.stats.location = loc
    # IDENTITY-DRIVEN NAMING (operator procedure 2026-10-29): for SUDS
    # sources — whose files carry no usable channel names — the reviewed
    # plan's target codes override the engine's rate-derived fallback
    # (DU EchoPro archives as EH* @ loc 60 on the server, where band-by-
    # rate would say HH*). miniSEED stays passthrough: its headers ARE
    # the names.
    if chan_map and source_format == "suds":
        for tr in st:
            o = (tr.stats.channel or "?")[-1:].upper()
            if o in chan_map:
                tr.stats.location, tr.stats.channel = chan_map[o]
    return st


def _worker_convert_day(job):
    """Pickle-safe worker. Opens its own DB connection, imports suds_convert
    internally (so the parent doesn't have to pass the module across pickle).
    `job` is a plain dict so it pickles cleanly.

    Per-day-job timeout: wraps the body in a SIGALRM-based 10 minute deadline.
    Days that legitimately convert finish in seconds (slowest unit-average
    observed so far: 27s/day on DDNE 2018), so a day exceeding 10 minutes
    is overwhelmingly likely to be a deadlock or pathological-data hang.
    On timeout, the worker returns status="timeout" cleanly; the pool
    continues with other days. The poison day goes to the day-level retry
    pass. Without this, a single bad day stalls the whole unit indefinitely
    (DDNE 2017 lost 17h, DDSW 2019 lost 4.5h before manual intervention,
    both 2026-06-03/04).
    """
    import sqlite3 as _sql
    import signal as _signal

    DAY_TIMEOUT_S = 600  # 10 minutes per day-job; see docstring

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"day-job exceeded {DAY_TIMEOUT_S}s wall-clock")

    # Arm the alarm. signal.alarm only fires when Python returns to the
    # interpreter loop; for CPU-spinning hangs (DDSW 2019 signature, 40% CPU)
    # this works because workers do return to Python periodically. For
    # futex_wait deadlocks (DDNE 2017 signature, 0% CPU) signal interrupts
    # the wait and TimeoutError propagates.
    _signal.signal(_signal.SIGALRM, _timeout_handler)
    _signal.alarm(DAY_TIMEOUT_S)

    sys.path.insert(0, job["disk_to_sds"])
    import suds_convert  # noqa: E402
    conn = _sql.connect(job["db_path"])
    d_iso = job["date"]
    try:
        sta = job["station"]
        net = job["network"]
        loc = job["location"]
        d = date.fromisoformat(d_iso)
        recorder_eff = job["recorder_eff"]
        staging = job["staging_sds"]
        commit = job["commit"]
        floor_ratio = job["disk_size_floor_ratio"]

        selection_path = job.get("selection_path")
        decisions = None  # v0.4 decision list in selection mode
        jan1_tail_unavailable = False
        if selection_path:
            # v3 path: the selection database is the ONLY source of file
            # lists (fail-closed; validated once in main() before dispatch,
            # re-opened read-only here because workers are separate
            # processes). Legacy runtime selection is NOT a fallback.
            # Files are grouped by their allowed-channel set; each group is
            # one engine call with an orientation filter. Groups write
            # disjoint channel day-files, so order does not matter.
            from lib.selection import Selection
            sel = Selection(selection_path, net, sta, d.year, job["db_path"])
            blocked_pops = sel.blocked_for_day(d_iso)
            if blocked_pops:
                # Fail-closed PER DAY (Codex item 3): this day offers files
                # from population(s) whose channel content nobody could
                # establish. Operator identity work, not a conversion.
                sel.close()
                return {"status": "blocked_identity", "date": d_iso,
                        "n_files": 0, "n_boundary_files": 0,
                        "blocked_populations": [
                            f"{pk} ({basis})" for pk, basis in blocked_pops],
                        "sds_files_written": []}
            # v0.4 EXECUTION CONTRACT (Codex resolver review F3): the day's
            # work is its component_decision rows — per (file, orientation),
            # with authorized windows and explicit priorities. Midnight
            # spill arrives as the previous day's boundary_tail decision;
            # Jan 1's lives in the prior year's artifact.
            decisions = sel.decisions_for_day(d_iso)
            prev = d - timedelta(days=1)
            if prev.year != d.year:
                if job.get("prev_selection_path"):
                    psel = Selection(job["prev_selection_path"], net, sta,
                                     prev.year, job["db_path"])
                    decisions = list(decisions) + list(
                        psel.decisions_for_day(d_iso))
                    psel.close()
                else:
                    jan1_tail_unavailable = True
            if not decisions:
                if sel.files_for_day(d_iso):
                    # Rows exist but no decisions: a pre-v0.4 artifact.
                    sel.close()
                    return {"status": "error", "date": d_iso, "n_files": 0,
                            "n_boundary_files": 0, "sds_files_written": [],
                            "error": "selection has no component decisions "
                                     "(pre-v0.4 artifact) — recompile"}
                sel.close()
                return {"status": "no_files", "date": d_iso, "n_files": 0,
                        "n_boundary_files": 0, "sds_files_written": []}
            # Skip-unchanged fast path, fingerprinted over the DECISIONS
            # (path, orientation, window, priority) plus manifest-recorded
            # source identities and all code SHAs (F7).
            dec_rows = sorted((p, o, f"{ws}:{we}:{pr}")
                              for p, _pop, _f, o, ws, we, pr, _r in decisions)
            if commit and job.get("skip_unchanged"):
                src_ids = {}
                paths_only = sorted({r[0] for r in dec_rows})
                for i in range(0, len(paths_only), 500):
                    b = paths_only[i:i + 500]
                    qm = ",".join("?" * len(b))
                    for p, sz, mt in conn.execute(
                            f"SELECT path, size_bytes, mtime FROM files "
                            f"WHERE path IN ({qm})", b):
                        src_ids[p] = f"{sz}:{mt}"
                fp = _day_fingerprint(dec_rows, job.get("code_id", "?"),
                                      net, sta, loc, src_ids)
                prior = _load_skip_ledger(staging).get(d_iso)
                if prior and prior.get("fingerprint") == fp and _outputs_intact(prior):
                    sel.close()
                    return {"status": "skipped_unchanged", "date": d_iso,
                            "n_files": len(paths_only),
                            "n_boundary_files": 0,
                            "sds_files_written": [
                                [p, s] for p, s, *_ in prior.get("outputs", [])]}
                job["_fingerprint"] = fp
            sel.close()
        else:
            # Legacy (Branch-1) path: cross-source-aware runtime selection.
            # KNOWN ARCHITECTURAL DEFECT (operator-flagged 2026-10-18): this
            # recomputes selection in code; the plan's source_priority is
            # not consulted. See STATUS.md Project-1 caveat.
            files = query_cross_source_day_files(conn, sta, d, recorder_eff, floor_ratio)
            # Midnight-boundary fix (Option C): also pull day-1's HHMM='2359'
            # file(s) — these carry the first SS seconds of `d` that would
            # otherwise be lost when write_sds overwrites `d`'s SDS file from
            # scratch. See "Midnight-boundary data loss" in CLAUDE.md.
            boundary_files = query_boundary_tail_files(conn, sta, d, recorder_eff, floor_ratio)

        def _is_suds(path):
            return path.endswith((".dmx", ".dmx.gz"))

        def _combine(r, gr):
            if _RANK.get(gr.get("status"), 0) > _RANK.get(r.get("status"), 0):
                r["status"] = gr["status"]
                if gr.get("error"):
                    r["error"] = gr["error"]
            for k in ("n_files", "n_traces", "read_errors",
                      "bogus_year_traces_dropped", "n_recovered"):
                if k in gr:
                    r[k] = r.get(k, 0) + gr[k]
            r["sds_files_written"] = (r.get("sds_files_written", [])
                                      + gr.get("sds_files_written", []))
            return r

        def _one(group_files, orient_filter, b_files, resolve=False):
            # MIXED-FORMAT DAYS (evidence map row 7): a day directory can
            # hold BOTH SUDS and miniSEED (recorder transitions, ingest
            # quirks). One engine per day silently ignored the minority
            # format. Each format now goes to its own engine; outputs
            # land in disjoint-or-merged SDS day files via write_sds.
            # b_files (review F3): THIS call's boundary tails only — a tail
            # contributes solely to calls whose filter its own selected
            # channel set covers. A boundary-only call (empty group_files)
            # converts just the midnight sliver.
            suds = [f for f in group_files if _is_suds(f)]
            mseed = [f for f in group_files if not _is_suds(f)]
            b_suds = [f for f in b_files if _is_suds(f)]
            b_mseed = [f for f in b_files if not _is_suds(f)]
            engine_calls = []
            if suds or b_suds:
                engine_calls.append(("suds", lambda: convert_echopro_day(
                    suds_convert, sta, net, loc, suds, staging, commit,
                    boundary_files=b_suds, day=d, orient_filter=orient_filter,
                    resolve_mode=resolve)))
            if mseed or b_mseed:
                eng = convert_minimus_day if recorder_eff == "minimus" else convert_gecko_day
                engine_calls.append(("mseed", lambda: eng(
                    sta, net, loc, mseed, staging, commit,
                    suds_convert, boundary_files=b_mseed,
                    day=d, orient_filter=orient_filter,
                    resolve_mode=resolve)))
            # Review F5: on a mixed-format group, the format of the FIRST
            # file (a winner — fills sort last) writes first. write_sds puts
            # existing-file data before new data, so the first write wins
            # equal-time overlaps: the chooser's winner, not format order.
            lead = group_files[0] if group_files else (b_files[0] if b_files else None)
            if lead is not None and not _is_suds(lead):
                engine_calls.reverse()
            results = [call() for _fmt, call in engine_calls]
            if not results:
                return {"status": "no_files", "n_files": 0,
                        "n_boundary_files": len(b_files)}
            r = results[0]
            for gr in results[1:]:
                r = _combine(r, gr)
            if len(results) > 1:
                r["mixed_formats"] = True
            return r

        _RANK = {"error": 6, "timeout": 5, "parse_error": 4,
                 "unsupported_recorder": 4, "qc_flagged": 3, "ok": 2,
                 "no_files": 1}
        try:
            if decisions is None:  # legacy (--legacy-chooser) mode only
                r = _one(files, None, boundary_files)
            else:
                # DECISION-DRIVEN CONVERSION (Codex resolver review F1+F4):
                # each selected file is read ONCE by its own-format per-file
                # adapter — no cross-file and no cross-format merging — then
                # every decision clips that file's traces of its orientation
                # to the authorized window; the integer-ns resolver overlays
                # by explicit priority; the day is written ONCE.
                read_errors = []
                n_bogus = 0
                by_path = {}
                for path, _pop, fmt, orient, ws, we, prio, _rsn in decisions:
                    by_path.setdefault((path, fmt), []).append(
                        (orient, int(ws), int(we), int(prio)))
                mixed = len({fmt for _p, fmt in by_path}) > 1
                by_key = {}
                templates = {}
                for (path, fmt), drows in sorted(by_path.items()):
                    st = _read_file_traces(path, fmt, net, sta, loc,
                                           suds_convert, read_errors,
                                           job.get("chan_map"))
                    bog = []
                    st = _filter_bogus_year_traces(
                        st, DEFAULT_MIN_DATA_YEAR, bog)
                    n_bogus += len(bog)
                    for tr in st:
                        o = (tr.stats.channel or "?")[-1:].upper()
                        key = (tr.id, round(float(tr.stats.sampling_rate), 6))
                        for orient, ws, we, prio in drows:
                            if o != orient:
                                continue
                            piece = _clip_trace_ns(tr, ws, we)
                            if piece is None:
                                continue
                            templates.setdefault(key, tr.stats)
                            by_key.setdefault(key, []).append({
                                "t0_ns": piece[0],
                                "rate": float(tr.stats.sampling_rate),
                                "data": piece[1], "priority": prio,
                                "path": f"{path}#{orient}#{prio}"})
                from lib.resolver import resolve_segments
                from obspy import (Stream as _S, Trace as _T,
                                   UTCDateTime as _U)
                import numpy as _np
                resolved_stream = _S()
                for key in sorted(by_key):
                    pieces = resolve_segments(by_key[key])
                    prev_end = None
                    dtn = 1_000_000_000 // round(key[1])
                    for sgm in pieces:
                        if prev_end is not None and sgm["t0_ns"] < prev_end:
                            raise RuntimeError(
                                f"resolver overlap for {key}")
                        prev_end = sgm["t0_ns"] + len(sgm["data"]) * dtn
                        stats = templates[key].copy()
                        stats.starttime = _U(ns=sgm["t0_ns"])
                        data = _np.ascontiguousarray(sgm["data"])
                        stats.npts = len(data)
                        resolved_stream += _T(data=data, header=stats)
                resolved_stream = _trim_to_day(resolved_stream, d)
                r = {"status": "ok" if not read_errors else "qc_flagged",
                     "n_files": len(by_path), "n_boundary_files": 0,
                     "n_traces": len(resolved_stream),
                     "rate_hz": (resolved_stream[0].stats.sampling_rate
                                 if len(resolved_stream) else None),
                     "read_errors": len(read_errors),
                     "bogus_year_traces_dropped": n_bogus,
                     "sds_files_written": []}
                if mixed:
                    r["mixed_formats"] = True
                if read_errors:
                    r["error"] = str(read_errors[:3])
                if commit and len(resolved_stream) > 0:
                    written = _write_day_replace(
                        suds_convert, resolved_stream, staging,
                        net, sta, d)
                    r["sds_files_written"] = [(str(p), sz)
                                              for p, sz in written]
                if jan1_tail_unavailable:
                    r["jan1_tail_unavailable"] = True
        except TimeoutError as e:
            r = {"status": "timeout", "n_files": 0, "n_boundary_files": 0,
                 "error": str(e)}
        except Exception as e:
            r = {"status": "error", "n_files": 0, "n_boundary_files": 0,
                 "error": f"{type(e).__name__}: {e}",
                 "traceback": traceback.format_exc()}
        r["date"] = d_iso
        if (job.get("_fingerprint") and commit
                and r.get("status") in ("ok", "qc_flagged")
                and r.get("sds_files_written")):
            import json as _j
            # Outputs recorded with content hashes (review F7): same-size
            # corruption can no longer satisfy a later skip check.
            outs = []
            for p, s in r["sds_files_written"]:
                try:
                    outs.append([p, s, _file_sha256(p)])
                except OSError:
                    outs = None
                    break
            if outs is not None:
                rec = {"date": d_iso, "fingerprint": job["_fingerprint"],
                       "outputs": outs}
                with open(_skip_ledger_path(staging), "a") as _f:
                    _f.write(_j.dumps(rec) + "\n")
        return r
    except TimeoutError as e:
        # Timeout fired before files/boundary_files were even resolved.
        return {"status": "timeout", "n_files": 0, "n_boundary_files": 0,
                "error": str(e), "date": d_iso}
    finally:
        _signal.alarm(0)
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
    ap.add_argument("--skip-unchanged", action="store_true",
                    help="v3: skip days whose input fingerprint matches a "
                         "prior committed conversion in this staging root "
                         "with outputs intact (repairs/reruns fast path)")
    ap.add_argument("--selection", default=None,
                    help="v3: selection sqlite from coverage_planner. When "
                         "given, it is the ONLY source of per-day file lists "
                         "(validated fail-closed against this manifest DB "
                         "before any job is dispatched); runtime cross_source "
                         "selection is disabled.")
    ap.add_argument("--prev-selection", default=None,
                    help="v3: the PRIOR year's selection sqlite, used only "
                         "for the January-1 midnight boundary tail (review "
                         "F3). Without it, Jan 1 converts without its first "
                         "seconds and the day is flagged "
                         "jan1_tail_unavailable — the legacy tail query is "
                         "never used in selection mode.")
    ap.add_argument("--legacy-chooser", action="store_true",
                    help="EXPLICITLY use the v1 runtime cross_source chooser "
                         "instead of a selection. KNOWN ARCHITECTURAL DEFECT: "
                         "it does not obey the reviewed plan's source "
                         "selection (operator-flagged 2026-10-18). Exists "
                         "only for Branch-1 fallback comparisons; never for "
                         "production. Without --selection OR this flag, "
                         "phase3 refuses to run at all (review F1).")
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

    # Structural gate (review 2026-10-23, F1): conversion REQUIRES a compiled,
    # validated selection. The legacy runtime chooser — the v1 plan-not-obeyed
    # defect — is unreachable by omission; it takes an explicit flag.
    if args.selection and args.legacy_chooser:
        print("[phase3] REFUSED: --selection and --legacy-chooser are "
              "mutually exclusive.", file=sys.stderr, flush=True)
        sys.exit(2)
    if args.legacy_chooser and args.commit:
        # Codex fix-review F1: the defective chooser may never WRITE on
        # this branch — comparison runs are dry-run here; a committed
        # legacy conversion belongs to the frozen fallback branch only.
        print("[phase3] REFUSED: --legacy-chooser cannot be combined with "
              "--commit on this branch (plan-not-obeyed defect). Dry-run "
              "comparisons only; committed legacy conversions run from the "
              "rewrite-suds2sds checkout.", file=sys.stderr, flush=True)
        sys.exit(2)
    if not args.selection and not args.legacy_chooser:
        print("[phase3] REFUSING TO RUN: no --selection given. v3 converts "
              "only what a compiled selection admits (fail-closed). Build one "
              "with scan/coverage_planner.py. To knowingly run the v1 runtime "
              "chooser pass --legacy-chooser (KNOWN DEFECT: it does not obey "
              "the reviewed plan's source selection).",
              file=sys.stderr, flush=True)
        sys.exit(2)
    if args.legacy_chooser:
        print("[phase3] WARNING: --legacy-chooser — v1 runtime cross_source "
              "selection in use; the reviewed plan's source selection is NOT "
              "obeyed (operator-flagged defect 2026-10-18). Comparison/"
              "fallback use only — never production.", flush=True)

    import subprocess as _sp
    try:
        engine_sha = _sp.run(["git", "-C", args.disk_to_sds, "rev-parse", "HEAD"],
                             capture_output=True, text=True).stdout.strip()[:12] or "?"
    except Exception:
        engine_sha = "?"

    import yaml
    plan = yaml.safe_load(open(args.plan))
    station = plan["station"]
    network = plan["network"]
    location = plan.get("location", "00")
    status = plan["status"]

    print(f"[phase3] station={network}.{station} loc={location!r} status={status} "
          f"commit={args.commit} dry-run={not args.commit}", flush=True)

    # Plan gate (review 2026-10-23, F1): only a reviewed, approved plan may
    # write. defer_conversion exits cleanly further down; every other
    # non-'ok' status (needs_review, BLOCKED, ...) refuses to commit.
    if args.commit and status not in ("ok", "defer_conversion"):
        print(f"[phase3] PLAN GATE REFUSED: plan status={status!r} — only "
              f"status 'ok' plans may convert with --commit. Review and "
              f"approve the plan first.", file=sys.stderr, flush=True)
        sys.exit(2)

    # Fail-fast: confirm suds_convert is importable from --disk-to-sds before
    # we spin up the pool. Each worker will re-import (cheap; Python caches).
    sys.path.insert(0, args.disk_to_sds)
    import suds_convert  # noqa: F401,E402  (probe import only)

    # Composite code identity (review 2026-10-23 F7): every layer whose
    # change must invalidate skip-unchanged — this repo, the engine repo,
    # sudspy — plus the digest of the selection artifact itself (which in
    # turn binds the manifest identity).
    _here_dir = os.path.dirname(os.path.abspath(__file__))
    def _sha_of_repo(p):
        try:
            return (_sp.run(["git", "-C", p, "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=10)
                    .stdout.strip()[:12] or "?")
        except Exception:
            return "?"
    project_sha = _sha_of_repo(_here_dir)
    try:
        import sudspy as _sudspy_mod
        sudspy_sha = _sha_of_repo(os.path.dirname(os.path.dirname(
            os.path.abspath(_sudspy_mod.__file__))))
    except Exception:
        sudspy_sha = "?"
    selection_sha = None
    if args.selection:
        selection_sha = hashlib.sha256(
            open(args.selection, "rb").read()).hexdigest()
    code_id = "|".join([project_sha, engine_sha, sudspy_sha,
                        (selection_sha or "-")[:16]])

    # Unit-level watchdog: force-kills the whole phase3 process after a hard
    # wall-clock deadline. Catches the multiprocessing.Pool finalisation hangs
    # (LOYU 2016 2026-06-06, DDWB 2022 2026-06-04) that the per-day-job
    # SIGALRM in _worker_convert_day does not cover.
    _start_phase3_watchdog(deadline_s=10800)  # 3 hours

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
            # Review 2026-10-23 (F2/F7): the run manifest names the exact
            # selection artifact and every code layer that produced bytes.
            "selection_path": args.selection,
            "selection_sha": selection_sha,
            "engine_sha": engine_sha,
            "sudspy_sha": sudspy_sha,
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
        # Per-epoch channel targets from the reviewed plan (orientation ->
        # (location, channel)); empty when the plan carries none.
        ep_chan_map = {}
        for ch in (ep.get("channels") or []):
            tgt = str(ch.get("target") or "")
            parts = tgt.split(".")
            if len(parts) == 4 and parts[3]:
                ep_chan_map[parts[3][-1].upper()] = (parts[2], parts[3])
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
                "selection_path": args.selection,
                "chan_map": ep_chan_map or None,
                "prev_selection_path": args.prev_selection,
                "skip_unchanged": args.skip_unchanged,
                "code_id": code_id,
            })
            if args.limit_days and len(jobs) >= args.limit_days:
                break
        else:
            continue
        break

    if not jobs:
        # Requested dates fall outside every supported plan epoch. Nothing
        # to convert — exit cleanly (the auditor still measures the day
        # against the sources, so a wrongly-narrow plan surfaces as a
        # missing-day FAIL with evidence, not as a confusing refusal here).
        print("[phase3] 0 day-jobs: requested date(s) outside the plan's "
              "supported epochs — nothing to convert", flush=True)
        sys.exit(0)

    if args.selection:
        # Fail-closed validation BEFORE dispatch: schema, completeness,
        # net/sta/year, and manifest-identity binding. A selection compiled
        # from a different or regenerated index refuses to run.
        from lib.selection import Selection, SelectionError
        years = sorted({int(j["date"][:4]) for j in jobs})
        if len(years) > 1:
            print(f"[phase3] ERROR: --selection covers one year; jobs span {years}",
                  flush=True)
            sys.exit(2)
        try:
            # deep=True (review F2): once per run, verify the manifest's
            # full content hash, the generator == running code, and that
            # every selected path exists in the manifest. Workers repeat
            # only the cheap identity check per day.
            _sel = Selection(args.selection, network, station,
                             years[0], args.db, deep=True)
            n_sel_days = len(_sel.days())
            _sel.close()
        except SelectionError as e:
            print(f"[phase3] SELECTION REFUSED: {e}", flush=True)
            sys.exit(2)
        print(f"[phase3] selection validated: {args.selection} "
              f"({n_sel_days} days on record)", flush=True)

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
        iso = r.get("date", "?")
        if run_manifest_state is not None:
            per_date_status.append({
                "date": iso,
                "status": r["status"],
                "n_files": r.get("n_files", 0),
                "n_boundary_files": r.get("n_boundary_files", 0),
                "n_traces": r.get("n_traces", 0),
                "rate_hz": r.get("rate_hz"),
                "read_errors": r.get("read_errors", 0),
                "bytes_written": day_bytes,
                "error": r.get("error", ""),
                "bulk_fallback_used": r.get("bulk_fallback_used", False),
                # Fix-review F3: promotion must be able to gate on a Jan 1
                # converted without its prior-year boundary tail.
                "jan1_tail_unavailable": r.get("jan1_tail_unavailable", False),
                "sds_files_written": [[p, s] for p, s in
                                      r.get("sds_files_written", [])],
            })
        if r["status"] == "error":
            print(f"  [{iso}] ERROR {r.get('error')}", flush=True)
            if "traceback" in r:
                print(r["traceback"], flush=True)
            return
        bulk_fb = " bulk_fallback=Y" if r.get("bulk_fallback_used") else ""
        print(f"  [{iso}] {r['status']:10} files={r['n_files']:>5} "
              f"bf={r.get('n_boundary_files',0):>1} "
              f"traces={r.get('n_traces',0):>3} "
              f"rate={r.get('rate_hz') or 0:>5} "
              f"errs={r.get('read_errors',0)} "
              f"recovered={r.get('n_recovered',0)} "
              f"dropped={r.get('dropped_components',[])} "
              f"bogus_yr={r.get('bogus_year_traces_dropped',0)}"
              f"{bulk_fb} "
              f"WRITE={n_written}", flush=True)

    # Tracks whether the parallel loop bailed early because Pool got stuck
    # (heartbeat below). When True, the manifest is tagged so apply.py and
    # the recovery register know the per_date_status is partial.
    partial_completion = False
    partial_completion_reason = ""

    if args.workers <= 1:
        # Serial path — keep for direct debugging and to isolate NFS effects.
        for job in jobs:
            _emit(_worker_convert_day(job))
    else:
        # Parallel path. imap_unordered streams results as workers finish,
        # so output appears continuously rather than in one final flush.
        #
        # Heartbeat-driven loop. The CPython stdlib Pool does NOT re-dispatch
        # a dead worker's in-flight task — when a worker is killed by glibc
        # for heap corruption (the SIGALRM-during-libmseed chain documented
        # in handoffs/disk_to_sds/2026-06-07_pool-teardown-hang/), MainThread
        # blocks forever in IMapIterator.next()._cond.wait() because _index
        # can never reach _length. Without this loop, the only escape is the
        # 3h unit-level watchdog. With it, we bail ~POOL_HEARTBEAT_S after
        # the last successful result, write a partial manifest, and exit 0.
        # apply.py promotes whatever days DID complete; the missing days are
        # logged for a targeted re-run via the recovery register.
        POOL_HEARTBEAT_S = 900  # 15 min — see _bail_threshold reasoning below
        from multiprocessing import Pool
        from multiprocessing import TimeoutError as MPTimeoutError
        with Pool(processes=args.workers) as pool:
            iter_ = pool.imap_unordered(_worker_convert_day, jobs)
            n_emitted = 0
            while True:
                try:
                    r = iter_.next(timeout=POOL_HEARTBEAT_S)
                except StopIteration:
                    break
                except MPTimeoutError:
                    # No result in POOL_HEARTBEAT_S since the last one.
                    # _bail_threshold reasoning: with 8 workers each capped
                    # at DAY_TIMEOUT_S=600s, the worst-case natural silence
                    # between batched-completion bursts is ~10 min. 15 min
                    # gives buffer for variance without false-triggering.
                    # When this fires it's reliably the dead-worker-task-loss
                    # hang, not a slow day.
                    partial_completion = True
                    partial_completion_reason = (
                        f"Pool hang detected: no result in {POOL_HEARTBEAT_S}s "
                        f"after {n_emitted}/{len(jobs)} day-jobs emitted. "
                        f"Likely SIGALRM-induced libmseed heap corruption "
                        f"killed worker(s) mid-task; standard library Pool "
                        f"does not re-dispatch lost tasks. Exiting cleanly "
                        f"with partial results — see handoff "
                        f"2026-06-07_pool-teardown-hang."
                    )
                    print(f"\n[phase3] HEARTBEAT: {partial_completion_reason}",
                          flush=True)
                    break
                else:
                    _emit(r)
                    n_emitted += 1

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
        # skipped_unchanged = hash-verified existing output (F7): succeeded.
        items_succeeded = n_ok + n_qc + results.get("skipped_unchanged", 0)
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
                "partial_completion": partial_completion,
                "partial_completion_reason": partial_completion_reason,
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

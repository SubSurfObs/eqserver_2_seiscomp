#!/usr/bin/env python3
"""Level-1 file-based scan of the EqServer archive.

Records one row per file from PATH + FILENAME + stat ONLY -- no file is opened.
Builds the `files` table of the manifest.

Work unit = (station, year). Finer than per-station so the worker pool load-balances:
the biggest single unit is ~one station-year (~0.5 M files, ~2 min), so a giant
multi-decade station no longer becomes a single-worker tail. Each unit writes its own
part-DB (no SQLite writer contention); parts are merged at the end.

`--no-db` walks+parses+counts only (isolates NFS walk cost).

Date authority is the FILENAME, not the directory path: real files sit under bogus
year dirs (e.g. a 2023 .ss under .../continuous/1900/01/01). Both are recorded; the
mismatch is flagged. `.ss` (Gecko kelunjimeta sidecar) is role=metadata, NOT discarded.

Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

EXT_TABLE = [
    (".dmx.gz", "echopro", "waveform"),
    (".dmx", "echopro", "waveform"),
    (".ss", "gecko", "metadata"),
    (".ms.zip", "gecko", "waveform"),
    (".ms", "gecko", "waveform"),
    (".mseed.zip", "mseed", "waveform"),
    (".mseed", "mseed", "waveform"),
]
FLAG_TOKENS = {"wrno", "trig", "ss"}

DATE_DASH = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_COMPACT = re.compile(r"^(\d{8})$")
HHMM = re.compile(r"^\d{3,4}$")
SS = re.compile(r"^\d{1,2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path            TEXT PRIMARY KEY,
    station         TEXT,
    dir_year        INTEGER,
    dir_month       INTEGER,
    dir_day         INTEGER,
    recorder_type   TEXT,
    source_type     TEXT,
    role            TEXT,
    file_year       INTEGER,
    file_month      INTEGER,
    file_day        INTEGER,
    date_mismatch   INTEGER,
    hhmm            TEXT,
    ss              TEXT,
    channel_suffix  TEXT,
    filename_station TEXT,
    station_mismatch INTEGER,
    flags           TEXT,
    size_bytes      INTEGER,
    mtime           REAL,
    exclude_reason  TEXT
);
"""
_INSERT = (
    "INSERT OR REPLACE INTO files (path, station, dir_year, dir_month, dir_day, "
    "recorder_type, source_type, role, file_year, file_month, file_day, date_mismatch, "
    "hhmm, ss, channel_suffix, filename_station, station_mismatch, flags, size_bytes, "
    "mtime, exclude_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _toint(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def classify_ext(name: str):
    low = name.lower()
    for ext, recorder, role in EXT_TABLE:
        if low.endswith(ext):
            return ext, recorder, role
    return None, "unknown", "unknown"


def parse_filename(name: str, dir_station: str) -> dict:
    rec = {
        "recorder_type": "unknown", "source_type": "unknown", "role": "unknown",
        "file_year": None, "file_month": None, "file_day": None,
        "hhmm": None, "ss": None, "channel_suffix": None,
        "filename_station": None, "station_mismatch": None,
        "flags": None, "exclude_reason": None,
    }
    try:
        ext, recorder, role = classify_ext(name)
        rec["recorder_type"] = recorder
        rec["role"] = role
        if ext is None:
            rec["exclude_reason"] = "unknown_extension"
            return rec
        stem = name[: -len(ext)]

        flags = []
        changed = True
        while changed:
            changed = False
            for tok in FLAG_TOKENS:
                if stem.lower().endswith("." + tok):
                    flags.append(tok)
                    stem = stem[: -(len(tok) + 1)]
                    changed = True
        if flags:
            rec["flags"] = ",".join(sorted(flags))
        if "trig" in flags:
            rec["exclude_reason"] = "triggered"

        if " " in stem:
            rec["source_type"] = "telemetry"
            toks = stem.split()
        else:
            rec["source_type"] = "disk"
            toks = stem.split("_")

        if len(toks) < 3:
            rec["exclude_reason"] = rec["exclude_reason"] or "unparsed"
            return rec

        d = toks[0]
        m = DATE_DASH.match(d)
        if m:
            rec["file_year"], rec["file_month"], rec["file_day"] = int(m[1]), int(m[2]), int(m[3])
        elif DATE_COMPACT.match(d):
            rec["file_year"] = int(d[0:4]); rec["file_month"] = int(d[4:6]); rec["file_day"] = int(d[6:8])

        if len(toks) >= 2 and HHMM.match(toks[1]):
            rec["hhmm"] = toks[1]
        if len(toks) >= 4 and SS.match(toks[2]):
            rec["ss"] = toks[2]

        sta_tok = toks[-1]
        # Strip trailing event-index from triggered files: "LOCU.1" -> "LOCU"
        # (event-N suffix appears on .N.trig.dmx; the .trig was already stripped above)
        if "trig" in flags and "." in sta_tok:
            sta_root, _, tail = sta_tok.rpartition(".")
            if tail.isdigit():
                sta_tok = sta_root
        if "_" in sta_tok and rec["source_type"] == "telemetry":
            base, chan = sta_tok.split("_", 1)
            rec["filename_station"] = base
            rec["channel_suffix"] = chan
        else:
            rec["filename_station"] = sta_tok

        if rec["filename_station"] and dir_station:
            rec["station_mismatch"] = int(rec["filename_station"] != dir_station)

        if rec["exclude_reason"] is None:
            if rec["channel_suffix"]:
                rec["exclude_reason"] = "single_channel"
            elif rec["recorder_type"] == "unknown":
                rec["exclude_reason"] = "unknown_extension"
            elif rec["station_mismatch"]:
                rec["exclude_reason"] = "wrong_station"
        return rec
    except Exception as exc:
        rec["exclude_reason"] = f"parse_error:{type(exc).__name__}"
        return rec


def iter_day_dirs(cont_year_path, month_filter=None, day_filter=None):
    """Yield (day_path, month_int, day_int) under .../continuous/<year>.
    If month_filter / day_filter are set/list of ints, only matching values are yielded."""
    try:
        months = list(os.scandir(cont_year_path))
    except OSError:
        return
    for mo in months:
        if not mo.is_dir():
            continue
        if month_filter and _toint(mo.name) not in month_filter:
            continue
        for d in os.scandir(mo.path):
            if not d.is_dir():
                continue
            if day_filter and _toint(d.name) not in day_filter:
                continue
            yield d.path, _toint(mo.name), _toint(d.name)


def scan_unit(archive, station, year, part_db, month_filter=None, day_filter=None):
    """Scan one (station, year). Returns summary; writes part-DB unless part_db is None."""
    t0 = time.time()
    cont_year = os.path.join(archive, station, "continuous", year)
    dy = _toint(year)
    n = 0
    by_recorder = defaultdict(int)
    n_excluded = n_meta = n_date_mismatch = 0
    conn = cur = None
    batch = []
    if part_db:
        conn = sqlite3.connect(part_db)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(SCHEMA)
        cur = conn.cursor()

    for day_path, dm, dd in iter_day_dirs(cont_year, month_filter, day_filter):
        for entry in os.scandir(day_path):
            if not entry.is_file(follow_symlinks=False):
                continue
            r = parse_filename(entry.name, station)
            st = entry.stat(follow_symlinks=False)
            n += 1
            by_recorder[r["recorder_type"]] += 1
            if r["exclude_reason"]:
                n_excluded += 1
            if r["role"] == "metadata":
                n_meta += 1
            date_mismatch = None
            if r["file_year"] is not None and dy is not None:
                date_mismatch = int((r["file_year"], r["file_month"], r["file_day"]) != (dy, dm, dd))
                n_date_mismatch += date_mismatch
            if cur is not None:
                batch.append((
                    entry.path, station, dy, dm, dd,
                    r["recorder_type"], r["source_type"], r["role"],
                    r["file_year"], r["file_month"], r["file_day"], date_mismatch,
                    r["hhmm"], r["ss"], r["channel_suffix"],
                    r["filename_station"], r["station_mismatch"], r["flags"],
                    st.st_size, st.st_mtime, r["exclude_reason"],
                ))
                if len(batch) >= 20000:
                    cur.executemany(_INSERT, batch); batch.clear()
    if cur is not None:
        if batch:
            cur.executemany(_INSERT, batch)
        conn.commit(); conn.close()
    return {
        "station": station, "year": year, "n_files": n,
        "by_recorder": dict(by_recorder), "n_excluded": n_excluded,
        "n_metadata": n_meta, "n_date_mismatch": n_date_mismatch,
        "elapsed": time.time() - t0, "part_db": part_db,
    }


def _worker(args):
    return scan_unit(*args)


def list_stations(archive):
    out = []
    for e in os.scandir(archive):
        if e.is_dir() and os.path.isdir(os.path.join(e.path, "continuous")):
            out.append(e.name)
    return sorted(out)


def list_units(archive, stations, year_filter=0):
    """Enumerate (station, year) work units. If year_filter>0, only that year."""
    units = []
    yr_str = str(year_filter) if year_filter else None
    for s in stations:
        cont = os.path.join(archive, s, "continuous")
        try:
            for y in os.scandir(cont):
                if y.is_dir() and (yr_str is None or y.name == yr_str):
                    units.append((s, y.name))
        except OSError:
            continue
    return units


def merge_parts(final_db, part_dbs):
    conn = sqlite3.connect(final_db)
    conn.executescript(SCHEMA)
    for p in part_dbs:
        if not p or not os.path.exists(p):
            continue
        conn.execute("ATTACH DATABASE ? AS part", (p,))
        conn.execute("INSERT OR REPLACE INTO files SELECT * FROM part.files")
        conn.commit()
        conn.execute("DETACH DATABASE part")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station ON files(station)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_recorder ON files(recorder_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_role ON files(role)")
    # Composite indexes for downstream consumers (plan_generator, metadata_harvest).
    # Built once at scan-merge time so consumers don't pay the one-time index cost on
    # a 7.9M-row mini DB (~3 min) or the eventual 232M-row full manifest.
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station_dir ON files(station, dir_year, dir_month, dir_day)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station_role ON files(station, role)")
    conn.commit(); conn.close()


def merge_parts_per_station(out_dir, station_to_parts, station_to_net,
                            workers=4, delete_parts=False):
    """Per-station merge: each station's year-parts go into one small per-station
    DB at <out_dir>/<NET>.<STA>.db. Uses the proven fast-build pattern (no PK
    constraint during insert, plain INSERT, UNIQUE+other indexes at end).

    Runs in parallel via ProcessPoolExecutor so 60+ stations finish in minutes
    not hours. Delegates to scan.build_per_station_dbs.build_one_station so
    there's a single source of truth for the per-station merge logic.

    Per-station DBs are the canonical Phase 3 input — phase3_driver.py takes
    one station's DB + plan and produces SDS for that station. Downstream
    tools all filter WHERE station = ?, so per-station works as a drop-in.
    """
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in _sys.path:
        _sys.path.insert(0, here)
    from build_per_station_dbs import build_one_station

    os.makedirs(out_dir, exist_ok=True)
    jobs = []
    for sta, parts in sorted(station_to_parts.items()):
        net = station_to_net.get(sta) or "UNK"
        jobs.append((sta, parts, net, out_dir, delete_parts))

    print(f"[level1->per-station] {len(jobs)} stations, workers={workers}", flush=True)
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(build_one_station, j): j[0] for j in jobs}
        for fut in as_completed(futures):
            sta, n, sz, el, np_ = fut.result()
            done += 1
            print(f"  [{done:>3}/{len(jobs)}] {sta:8s} parts={np_:>2} "
                  f"rows={n:>10,} db={sz:>6.0f}MB time={el:>5.1f}s", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Level-1 file-based archive scan")
    ap.add_argument("--archive", default="/mnt/eqserver_archive/shared/data/repository/archive")
    ap.add_argument("--db", default=os.path.expanduser("~/eqserver_manifest.db"))
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--stations", default="", help="comma-separated; overrides others")
    ap.add_argument("--stations-file", default="", help="newline-separated station list (# comments ok)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit-stations", type=int, default=0)
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--per-station-dbs", default="",
                    help="output one DB per station instead of a global merged DB. "
                         "Pass the output directory (created if missing). Each station "
                         "ends up at <dir>/<NET>.<STA>.db with full indexes. "
                         "Skips the global merge entirely — much faster at archive "
                         "scale and matches the Phase 3 per-station processing unit. "
                         "Mutually exclusive with --db.")
    ap.add_argument("--registry", default="",
                    help="path to station_registry.yaml (REQUIRED if --per-station-dbs, "
                         "used to look up target_network for the output filename)")
    ap.add_argument("--unit-log", default="", help="CSV: one row per completed (station,year) unit, written live")
    ap.add_argument("--year", type=int, default=0, help="restrict scan to one calendar year")
    ap.add_argument("--month", default="", help="month(s) within --year, comma-separated (e.g. '1' or '1,2,3'); empty = all")
    ap.add_argument("--day", default="", help="day(s) of month within --month, comma-separated (e.g. '1' or '1,2,3,4,5,6,7'); empty = all")
    args = ap.parse_args(argv)

    if args.stations:
        stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    elif args.stations_file:
        with open(args.stations_file) as f:
            stations = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    elif args.all:
        stations = list_stations(args.archive)
    else:
        ap.error("specify --stations, --stations-file, or --all")
    if args.limit_stations:
        stations = stations[: args.limit_stations]

    units = list_units(args.archive, stations, args.year)
    part_dir = args.db + ".parts"
    if not args.no_db:
        os.makedirs(part_dir, exist_ok=True)

    month_filter = None
    if args.month:
        month_filter = frozenset(int(m.strip()) for m in args.month.split(",") if m.strip())
    day_filter = None
    if args.day:
        day_filter = frozenset(int(d.strip()) for d in args.day.split(",") if d.strip())

    tasks = []
    for s, y in units:
        part = None if args.no_db else os.path.join(part_dir, f"{s}__{y}.sqlite")
        if part and os.path.exists(part):
            os.remove(part)
        tasks.append((args.archive, s, y, part, month_filter, day_filter))

    print(f"{len(stations)} stations -> {len(units)} (station,year) units; "
          f"{args.workers} workers; db={'<none>' if args.no_db else args.db}"
          + (f"; unit-log={args.unit_log}" if args.unit_log else ""),
          flush=True)

    unit_log_fh = unit_log_w = None
    if args.unit_log:
        unit_log_fh = open(args.unit_log, "w", newline="")
        unit_log_w = csv.writer(unit_log_fh)
        unit_log_w.writerow([
            "station", "year", "n_files", "elapsed_s",
            "n_excluded", "n_metadata", "n_date_mismatch", "by_recorder",
        ])

    summaries = []

    def _record(summary):
        summaries.append(summary)
        if unit_log_w:
            unit_log_w.writerow([
                summary["station"], summary["year"], summary["n_files"],
                f"{summary['elapsed']:.2f}", summary["n_excluded"],
                summary["n_metadata"], summary["n_date_mismatch"],
                json.dumps(summary["by_recorder"], sort_keys=True),
            ])
            unit_log_fh.flush()

    t0 = time.time()
    done = 0
    try:
        if args.workers <= 1:
            for t in tasks:
                _record(_worker(t)); done += 1
                if done % 50 == 0 or done == len(tasks):
                    _progress(done, len(tasks), summaries, t0)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                futs = [ex.submit(_worker, t) for t in tasks]
                for f in as_completed(futs):
                    _record(f.result()); done += 1
                    if done % 50 == 0 or done == len(tasks):
                        _progress(done, len(tasks), summaries, t0)
    finally:
        if unit_log_fh:
            unit_log_fh.close()
    wall = time.time() - t0

    if args.per_station_dbs:
        if not args.registry:
            print("ERROR: --per-station-dbs requires --registry", flush=True)
            return
        import yaml
        reg = yaml.safe_load(open(args.registry))
        station_to_net = {s: v.get("target_network")
                          for s, v in reg.items()
                          if isinstance(v, dict) and v.get("include")}
        station_to_parts = defaultdict(list)
        for s in summaries:
            sta = s.get("station") or os.path.basename(s["part_db"]).split("__")[0]
            station_to_parts[sta].append(s["part_db"])
        tm = time.time()
        # Workers for the per-station merge: capped at 4 (per-station builds
        # are SQLite-bound and oversaturate quickly past 4).
        ps_workers = min(4, max(1, args.workers // 4))
        merge_parts_per_station(args.per_station_dbs, dict(station_to_parts),
                                station_to_net, workers=ps_workers, delete_parts=False)
        print(f"per-station merge: {len(station_to_parts)} stations -> {args.per_station_dbs}/ "
              f"in {time.time()-tm:.1f}s", flush=True)
    elif not args.no_db:
        tm = time.time()
        merge_parts(args.db, [s["part_db"] for s in summaries])
        print(f"merged {len(summaries)} parts -> {args.db} in {time.time()-tm:.1f}s", flush=True)

    _final_report(summaries, wall)


def _progress(done, total, summaries, t0):
    files = sum(s["n_files"] for s in summaries)
    el = time.time() - t0
    rec = defaultdict(int); excl = meta = dm = 0
    slowest = ("", 0.0); biggest = ("", 0)
    for s in summaries:
        for k, v in s["by_recorder"].items():
            rec[k] += v
        excl += s["n_excluded"]; meta += s["n_metadata"]; dm += s["n_date_mismatch"]
        if s["elapsed"] > slowest[1]:
            slowest = (f"{s['station']}/{s['year']}", s["elapsed"])
        if s["n_files"] > biggest[1]:
            biggest = (f"{s['station']}/{s['year']}", s["n_files"])
    rec_s = " ".join(f"{k}={v:,}" for k, v in sorted(rec.items()))
    print(
        f"  [{done:>5}/{total}] {files:>13,} files  {el:7.1f}s  "
        f"{files/el:>9,.0f} f/s | {rec_s} | excl={excl:,} meta={meta:,} dm={dm:,}\n"
        f"     biggest unit so far: {biggest[0]} ({biggest[1]:,} files)   "
        f"slowest: {slowest[0]} ({slowest[1]:.0f}s)",
        flush=True,
    )


def _final_report(summaries, wall):
    total = sum(s["n_files"] for s in summaries)
    excl = sum(s["n_excluded"] for s in summaries)
    meta = sum(s["n_metadata"] for s in summaries)
    dm = sum(s["n_date_mismatch"] for s in summaries)
    rec = defaultdict(int)
    per_station = defaultdict(int)
    for s in summaries:
        for k, v in s["by_recorder"].items():
            rec[k] += v
        per_station[s["station"]] += s["n_files"]
    top = sorted(per_station.items(), key=lambda kv: -kv[1])[:10]
    print("=" * 64)
    print(f"units         : {len(summaries)}   stations: {len(per_station)}")
    print(f"files         : {total:,}")
    print(f"  by recorder : {dict(rec)}")
    print(f"  excluded    : {excl:,}   metadata(.ss): {meta:,}   date_mismatch: {dm:,}")
    print(f"wall          : {wall:.1f}s")
    print(f"throughput    : {total/wall:,.0f} files/s")
    print("top stations by files:")
    for st, n in top:
        print(f"  {st:12s} {n:>13,}")


if __name__ == "__main__":
    sys.exit(main())

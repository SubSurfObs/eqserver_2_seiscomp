#!/usr/bin/env python3
"""channel_epoch_scan.py — discover when a recorder's channel set changed.

Per (station, source_kind), sample one file at coarse intervals (~monthly),
read its mseed/SUDS header to find which channels the recorder was writing
at that point, and detect changes. When a change is found between two
samples, bisect by date to pin the epoch boundary to a small window.

Output: metadata/source_stats/_epochs/<STA>.json with a per-source-kind
timeline of "channel epochs":

  {
    "STBK": {
      "disk_mseed_numeric_date": [
        {"start": "2018-01-01", "end": "2022-09-30", "channels": ["CHE","CHN","CHZ"], "samples_used": 12, "n_files_at_boundary": 1440},
        {"start": "2022-10-01", "end": "2023-12-31", "channels": ["CHZ"],             "samples_used": 14, "n_files_at_boundary": 1440}
      ],
      "tele_ss_mseed":      [...],
      "tele_noss_mseed":    [...]
    }
  }

The epoch list lets the planner answer "what channels did source kind K
have at time T?" without opening any files at conversion time. The
sampling cost is bounded — ~12 reads per year per source_kind, plus
bisection (typically 4-8 reads) per detected change.

USAGE:
  python3 scan/channel_epoch_scan.py STBK
  python3 scan/channel_epoch_scan.py STBK --out-dir metadata/source_stats/_epochs

Notes:
- Reads files via their mirror_src path if present in the test env;
  otherwise via EqServer NFS (slower, but cached after first read).
- Tolerant of read errors (corrupt files etc.); just records "read_error"
  and continues.
"""
from __future__ import annotations
import argparse
import io
import json
import sqlite3
import sys
import warnings
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "metadata" / "source_stats" / "_epochs"


# Filename grammar buckets (mirrors discovery_audit but grouped by data shape)
# Each entry is a (regex pattern, fmt) — fmt ∈ {mseed, suds}
import re
SOURCE_KINDS = [
    # SUDS — read with sudspy.scan_suds_file
    ("disk_suds",      re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_\d{2}_\w+\.dmx(\.gz)?$"), "suds"),
    ("tele_ss_suds",   re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.dmx(\.gz)?$"), "suds"),
    ("tele_noss_suds", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.dmx(\.gz)?$"),       "suds"),
    # MSEED — read with obspy
    ("disk_mseed",     re.compile(r"^\d{8}_\d{4}_\w+\.ms\.zip$"),                     "mseed"),
    ("tele_ss_mseed",  re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.ms\.zip$"),    "mseed"),
    ("tele_noss_mseed",re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ms\.zip$"),          "mseed"),
    # Mixed-separator MSEED — surfaced 2026-06-22 (~1.73M files at the
    # RT130 cohort + WPSH + DDWB). See MIXED_SHAPES in scan/level1.py.
    ("tele_underscore_mseed", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4}_\w+\.ms\.zip$"),    "mseed"),
    ("tele_dasharound_mseed", re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}-\w+\.ms\.zip$"),    "mseed"),
    ("tele_alldash_mseed",    re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}_\w+\.ms\.zip$"),    "mseed"),
    # Per-channel MSEED — Minimus borehole stations (DDBE/DDWB/SCM2).
    # One file = one channel per minute. Tagged exclude_reason='single_channel'
    # in the manifest. We derive the channel SET from manifest filename
    # suffixes (channel_suffix column) instead of opening files — see
    # read_channels_perchan_from_manifest below. The fmt 'perchan' triggers
    # the special path.
    ("perchan_mseed",  re.compile(r"^.+_[A-Z][A-Z][A-Z0-9]\.mseed(\.zip)?$"),           "perchan"),
]


# --------------------------------------------------------------------------
# Velocity-channel filter
# --------------------------------------------------------------------------

def is_velocity_channel(ch: str) -> bool:
    """Return True for the velocity-seismometer channels we care about.

    Handles the three naming families on the EqServer archive:
      - SUDS native (EchoPro): c01/c02/c03 are velocity; c04+ are aux/mic
      - Echo native:            Up-T/North-T/East-T = velocity (translation);
                                Up-A/North-A/East-A = accelerometer (drop)
      - SEED format (Gecko/Minimus/Reftek): 3-letter XYZ where instrument
                                code (2nd letter) tells us H=velocity vs
                                N=accelerometer etc. Microphone CDO etc.
                                fall out.
    """
    if not ch:
        return False
    # Echo translation channels — keep
    if ch.endswith("-T"):
        return True
    if ch.endswith("-A"):
        return False
    # SUDS native c01/c02/c03 — keep; c04+ drop
    if len(ch) == 3 and ch[0].lower() == "c" and ch[1:].isdigit():
        try:
            return 1 <= int(ch[1:]) <= 3
        except ValueError:
            return False
    # SEED format: instrument code (2nd letter) must be H (high-gain
    # velocity seismometer). Other letters: N=accel, L=lowgain, D=pressure,
    # O=outdoor-mic, J=rotation, Y=displacement.
    if len(ch) == 3 and ch[1].upper() == "H":
        return True
    return False


def filter_velocity(channels) -> set:
    """Drop non-velocity channels from a set."""
    if isinstance(channels, str):  # error string passthrough
        return channels
    return {c for c in channels if is_velocity_channel(c)}


def classify_kind(name: str) -> tuple[str, str] | None:
    for kind, regex, fmt in SOURCE_KINDS:
        if regex.match(name):
            return (kind, fmt)
    return None


def read_channels_mseed(path: str) -> set | str:
    """Return set of channel names, or an error string."""
    try:
        warnings.filterwarnings("ignore")
        from obspy import read
        if path.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                channels = set()
                for n in zf.namelist():
                    if n.endswith((".ms", ".mseed")):
                        st = read(io.BytesIO(zf.read(n)), format="MSEED", headonly=True)
                        channels.update(tr.stats.channel for tr in st)
                return channels
        else:
            st = read(path, format="MSEED", headonly=True)
            return {tr.stats.channel for tr in st}
    except Exception as e:
        return f"read_error: {type(e).__name__}"


def read_channels_suds(path: str) -> set | str:
    try:
        warnings.filterwarnings("ignore")
        import sudspy
        entries = sudspy.scan_suds_file(path)
        channels = set()
        for e in entries:
            ch = e.get("channel", "")
            # Strip 'NET.STA.cNN' if present, keep component
            if "." in ch:
                ch = ch.split(".")[-1]
            channels.add(ch)
        return channels
    except Exception as e:
        return f"read_error: {type(e).__name__}"


def read_channels_perchan_from_manifest(conn, sta: str, year: int, month: int) -> set | str:
    """For Minimus per-channel mseed: collect distinct channel_suffix values
    in the month. Each file carries ONE channel; the set across files is
    the recorder's channel complement.

    Reads single_channel-tagged rows (which the standard pick_file SQL
    filter rejects), so this bypasses the normal flow."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT channel_suffix FROM files "
            "WHERE station = ? AND dir_year = ? AND dir_month = ? "
            "  AND exclude_reason = 'single_channel' "
            "  AND recorder_type = 'mseed' "
            "  AND channel_suffix IS NOT NULL",
            (sta, year, month)
        ).fetchall()
        return {r[0] for r in rows if r[0]}
    except Exception as e:
        return f"read_error: {type(e).__name__}"


def read_channels(path: str, fmt: str, conn=None, sta=None,
                  year=None, month=None) -> set | str:
    if fmt == "perchan":
        # No file open — use the manifest's channel_suffix column.
        if conn is None or sta is None or year is None or month is None:
            return "read_error: perchan needs (conn, sta, year, month)"
        chs = read_channels_perchan_from_manifest(conn, sta, year, month)
        return filter_velocity(chs)
    if fmt == "suds":
        return filter_velocity(read_channels_suds(path))
    return filter_velocity(read_channels_mseed(path))


# --------------------------------------------------------------------------
# Sampling: monthly + bisection
# --------------------------------------------------------------------------

def pick_file(conn: sqlite3.Connection, sta: str, kind: str,
              fmt: str, year: int, month: int) -> str | None:
    """Find any file for (sta, year, month) whose basename matches `kind`'s
    filename pattern. Filter in Python — keeps SQL trivial (uses the
    station+year+month index) so this stays fast even on multi-GB DBs.

    For kind='perchan_mseed' the manifest tags files exclude_reason=
    'single_channel'; we allow those through here so the perchan reader
    (which only checks for presence, not the file's contents) sees them.
    """
    if kind == "perchan_mseed":
        excl_clause = "(exclude_reason IS NULL OR exclude_reason = 'single_channel')"
    else:
        excl_clause = "exclude_reason IS NULL"
    rows = conn.execute(
        f"SELECT path FROM files "
        f"WHERE station = ? AND dir_year = ? AND dir_month = ? "
        f"  AND role != 'metadata' AND {excl_clause} "
        f"LIMIT 200",
        (sta, year, month)
    ).fetchall()
    for (path,) in rows:
        name = path.split("/")[-1]
        m = classify_kind(name)
        if m and m[0] == kind:
            return path
    return None


def pick_file_for_day(conn: sqlite3.Connection, sta: str, kind: str,
                     fmt: str, year: int, month: int, day: int) -> str | None:
    """Like pick_file but constrained to a single day."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE station = ? AND dir_year = ? AND dir_month = ? AND dir_day = ? "
        "  AND role != 'metadata' AND exclude_reason IS NULL "
        "LIMIT 200",
        (sta, year, month, day)
    ).fetchall()
    for (path,) in rows:
        name = path.split("/")[-1]
        m = classify_kind(name)
        if m and m[0] == kind:
            return path
    return None


def pick_file_near_month(conn: sqlite3.Connection, sta: str, kind: str,
                        fmt: str, target_year: int, target_month: int,
                        window_months: int = 2) -> tuple[str | None, int | None, int | None]:
    """Try (target_year, target_month) first, then expand outward by month
    up to ±window_months. Returns (path, found_year, found_month)."""
    candidates: list[tuple[int, int]] = [(target_year, target_month)]
    for offset in range(1, window_months + 1):
        for sign in (-1, 1):
            ym = target_year * 12 + (target_month - 1) + sign * offset
            y = ym // 12
            m = (ym % 12) + 1
            candidates.append((y, m))
    for (y, m) in candidates:
        path = pick_file(conn, sta, kind, fmt, y, m)
        if path:
            return path, y, m
    return None, None, None


def pick_file_near_day(conn: sqlite3.Connection, sta: str, kind: str,
                      fmt: str, target_year: int, target_month: int,
                      target_day: int, window_days: int = 7) -> tuple[str | None, date | None]:
    """Probe target day, then expand outward up to ±window_days."""
    target = date(target_year, target_month, target_day)
    for offset in range(0, window_days + 1):
        for sign in (1, -1) if offset > 0 else (1,):
            probe = target + timedelta(days=sign * offset)
            path = pick_file_for_day(conn, sta, kind, fmt,
                                     probe.year, probe.month, probe.day)
            if path:
                return path, probe
    return None, None


def bisect_transition(conn: sqlite3.Connection, sta: str, kind: str, fmt: str,
                     lo_date: date, hi_date: date,
                     lo_channels: tuple, hi_channels: tuple,
                     target_resolution_days: int = 30) -> dict:
    """Pin a channel-set transition between two sampled dates.

    Phase 1: bisect by months. Each probe samples a file from the middle
    month and classifies as matching lo_channels, hi_channels, or a third
    set (ambiguous → stop).

    Phase 2: once the window is ≤ ~60 days, drill to day-level probes to
    pin the boundary to the requested resolution.

    Returns: dict with the bisection window, probe trace, and resolution.
    """
    lo = lo_date
    hi = hi_date
    probes: list[dict] = []
    ambiguous: list[dict] = []
    max_probes = 16   # generous cap; typically resolves in 4-7

    while (hi - lo).days > target_resolution_days and len(probes) < max_probes:
        span_days = (hi - lo).days

        if span_days > 60:
            # Month-level probing
            mid = lo + timedelta(days=span_days // 2)
            path, found_y, found_m = pick_file_near_month(
                conn, sta, kind, fmt, mid.year, mid.month)
            if not path:
                break
            channels = read_channels(path, fmt, conn=conn, sta=sta,
                                     year=found_y, month=found_m)
            probe_date = date(found_y, found_m, 15)
            if isinstance(channels, str):
                probes.append({"date": probe_date.isoformat(),
                              "path": path, "level": "month",
                              "error": channels})
                break
            ch_tuple = tuple(sorted(channels))
            probes.append({"date": probe_date.isoformat(), "path": path,
                          "level": "month", "channels": sorted(channels)})
            if ch_tuple == lo_channels:
                if probe_date <= lo:
                    break
                lo = probe_date
            elif ch_tuple == hi_channels:
                if probe_date >= hi:
                    break
                hi = probe_date
            else:
                ambiguous.append({"date": probe_date.isoformat(),
                                 "channels": sorted(channels), "path": path})
                break
        else:
            # Day-level probing
            mid = lo + timedelta(days=span_days // 2)
            path, found_d = pick_file_near_day(
                conn, sta, kind, fmt, mid.year, mid.month, mid.day)
            if not path:
                break
            channels = read_channels(path, fmt, conn=conn, sta=sta,
                                     year=found_d.year, month=found_d.month)
            if isinstance(channels, str):
                probes.append({"date": found_d.isoformat(), "path": path,
                              "level": "day", "error": channels})
                break
            ch_tuple = tuple(sorted(channels))
            probes.append({"date": found_d.isoformat(), "path": path,
                          "level": "day", "channels": sorted(channels)})
            if ch_tuple == lo_channels:
                if found_d <= lo:
                    break
                lo = found_d
            elif ch_tuple == hi_channels:
                if found_d >= hi:
                    break
                hi = found_d
            else:
                ambiguous.append({"date": found_d.isoformat(),
                                 "channels": sorted(channels), "path": path})
                break

    return {
        "boundary_lo_date": lo.isoformat(),
        "boundary_hi_date": hi.isoformat(),
        "resolution_days": (hi - lo).days,
        "n_probes": len(probes),
        "probes": probes,
        "ambiguous": ambiguous,
    }


def scan_station_kind(conn: sqlite3.Connection, sta: str, kind: str,
                      fmt: str, bisect: bool = True,
                      target_resolution_days: int = 30) -> list[dict]:
    """Yearly sampling: pick one file from month 7 (or any month) per year
    for this (sta, kind). Detect channel-set changes year-over-year.

    When `bisect` is true (default), each transition detected at yearly
    resolution is bisected via month/day probes down to
    `target_resolution_days`. Cheap stations (single epoch across their
    lifetime) pay no bisection cost; only transition stations do."""
    # Quick range query — just min/max year that the station has data for
    rows = conn.execute(
        "SELECT MIN(dir_year), MAX(dir_year) FROM files WHERE station = ? "
        "AND role != 'metadata'",
        (sta,)
    ).fetchone()
    if not rows or not rows[0]:
        return []
    y_min, y_max = rows[0], rows[1]
    if y_min < 2010:
        # Manifest sometimes contains bogus pre-2010 dirs (e.g. 1900);
        # skip them
        y_min = max(y_min, 2010)

    samples = []
    for year in range(y_min, y_max + 1):
        # Try month 7 first (mid-year); fall back to other months if 7 has nothing
        path = None
        found_month = None
        for month in (7, 1, 4, 10, 6, 12, 2, 11, 3, 9, 5, 8):
            path = pick_file(conn, sta, kind, fmt, year, month)
            if path:
                found_month = month
                break
        if not path:
            continue
        channels = read_channels(path, fmt, conn=conn, sta=sta,
                                 year=year, month=found_month)
        if isinstance(channels, str):
            samples.append({"date": f"{year:04d}-07-01", "path": path,
                            "channels": None, "error": channels})
        else:
            samples.append({"date": f"{year:04d}-07-01", "path": path,
                            "channels": sorted(channels), "error": None})

    if not samples:
        return []

    # Build epochs by walking samples and grouping consecutive same-channel
    epochs = []
    current = None
    for s in samples:
        if s["channels"] is None:
            continue
        ch_tuple = tuple(s["channels"])
        if current is None or ch_tuple != current["channel_tuple"]:
            if current is not None:
                epochs.append(current)
            current = {
                "channel_tuple": ch_tuple,
                "channels": list(ch_tuple),
                "start": s["date"],
                "end": s["date"],
                "samples_used": 1,
                "sample_paths": [s["path"]],
            }
        else:
            current["end"] = s["date"]
            current["samples_used"] += 1
            if len(current["sample_paths"]) < 3:
                current["sample_paths"].append(s["path"])
    if current is not None:
        epochs.append(current)

    # Bisect transitions
    if bisect and len(epochs) > 1:
        for i in range(len(epochs) - 1):
            ep_a = epochs[i]
            ep_b = epochs[i + 1]
            lo_d = date.fromisoformat(ep_a["end"])
            hi_d = date.fromisoformat(ep_b["start"])
            lo_ch = tuple(ep_a["channels"])
            hi_ch = tuple(ep_b["channels"])
            print(f"    bisecting {kind} transition "
                  f"{ep_a['end']}→{ep_b['start']} "
                  f"{lo_ch}→{hi_ch}", flush=True)
            bnd = bisect_transition(
                conn, sta, kind, fmt, lo_d, hi_d, lo_ch, hi_ch,
                target_resolution_days=target_resolution_days)
            ep_a["end"] = bnd["boundary_lo_date"]
            ep_b["start"] = bnd["boundary_hi_date"]
            ep_a["boundary_after"] = bnd
            ep_b["boundary_before"] = bnd
            ep_a["boundary_resolution_days"] = bnd["resolution_days"]
            ep_b["boundary_resolution_days"] = bnd["resolution_days"]
            print(f"      → window {bnd['boundary_lo_date']}..{bnd['boundary_hi_date']} "
                  f"({bnd['resolution_days']}d, {bnd['n_probes']} probes)",
                  flush=True)

    # Strip helper field, return
    for e in epochs:
        e.pop("channel_tuple", None)
    return epochs


def scan_station(sta: str, db_path: Path, bisect: bool = True,
                 target_resolution_days: int = 30) -> dict:
    conn = sqlite3.connect(db_path)
    out: dict = {
        "station": sta,
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_db": str(db_path),
        "bisection_enabled": bisect,
        "target_resolution_days": target_resolution_days,
        "by_source_kind": {},
    }
    for kind, _regex, fmt in SOURCE_KINDS:
        print(f"  scanning {kind} ({fmt}) ...", flush=True)
        epochs = scan_station_kind(conn, sta, kind, fmt,
                                  bisect=bisect,
                                  target_resolution_days=target_resolution_days)
        if epochs:
            out["by_source_kind"][kind] = epochs
            print(f"    found {len(epochs)} epoch(s) "
                  f"with {sum(e['samples_used'] for e in epochs)} samples",
                  flush=True)
    conn.close()
    return out


def format_text(d: dict) -> str:
    lines = []
    lines.append(f"# Channel-epoch scan: {d['station']}")
    lines.append(f"# Generated: {d['generated_at_utc']}")
    lines.append("")
    if not d["by_source_kind"]:
        lines.append("(no source kinds had readable files in this station)")
        return "\n".join(lines) + "\n"
    for kind, epochs in d["by_source_kind"].items():
        lines.append(f"## {kind}")
        for e in epochs:
            res = e.get("boundary_resolution_days")
            res_str = f"  [boundary ±{res}d]" if res is not None else ""
            lines.append(f"  {e['start']} → {e['end']}  channels={e['channels']}  "
                         f"(n_samples={e['samples_used']}){res_str}")
            bnd_a = e.get("boundary_after")
            if bnd_a and bnd_a.get("ambiguous"):
                for amb in bnd_a["ambiguous"]:
                    lines.append(f"    ⚠ ambiguous channel set at {amb['date']}: "
                                 f"{amb['channels']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta")
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--network", default="VW")
    ap.add_argument("--no-bisect", action="store_true",
                   help="Disable per-transition bisection (yearly granularity only)")
    ap.add_argument("--target-resolution-days", type=int, default=30,
                   help="Stop bisecting once boundary window ≤ this many days "
                        "(default 30)")
    args = ap.parse_args()

    db_path = args.db_dir / f"{args.network}.{args.sta}.db"
    if not db_path.exists():
        print(f"ERROR: manifest DB not found: {db_path}", file=sys.stderr)
        return 2

    print(f"[channel_epoch_scan] {args.sta}", flush=True)
    out = scan_station(args.sta, db_path,
                       bisect=not args.no_bisect,
                       target_resolution_days=args.target_resolution_days)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"{args.sta}.json"
    txt_path = args.out_dir / f"{args.sta}.txt"
    json_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    txt_path.write_text(format_text(out))
    print(f"[channel_epoch_scan] wrote {json_path}", flush=True)
    print(f"[channel_epoch_scan] wrote {txt_path}", flush=True)
    print()
    print(format_text(out), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

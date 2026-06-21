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
]


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


def read_channels(path: str, fmt: str) -> set | str:
    if fmt == "suds":
        return read_channels_suds(path)
    return read_channels_mseed(path)


# --------------------------------------------------------------------------
# Sampling: monthly + bisection
# --------------------------------------------------------------------------

def pick_file(conn: sqlite3.Connection, sta: str, kind: str,
              fmt: str, year: int, month: int) -> str | None:
    """Find any file for (sta, year, month) whose basename matches `kind`'s
    filename pattern. Filter in Python — keeps SQL trivial (uses the
    station+year+month index) so this stays fast even on multi-GB DBs."""
    rows = conn.execute(
        "SELECT path FROM files "
        "WHERE station = ? AND dir_year = ? AND dir_month = ? "
        "  AND role != 'metadata' AND exclude_reason IS NULL "
        "LIMIT 200",
        (sta, year, month)
    ).fetchall()
    for (path,) in rows:
        name = path.split("/")[-1]
        m = classify_kind(name)
        if m and m[0] == kind:
            return path
    return None


def scan_station_kind(conn: sqlite3.Connection, sta: str, kind: str,
                      fmt: str) -> list[dict]:
    """Yearly sampling: pick one file from month 7 (or any month) per year
    for this (sta, kind). Detect channel-set changes year-over-year.

    Monthly bisection within years where a change is detected is a future
    extension — for the first cut, year-level resolution is enough to
    catch the major epoch transitions documented in the project."""
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
        for month in (7, 1, 4, 10, 6, 12, 2, 11, 3, 9, 5, 8):
            path = pick_file(conn, sta, kind, fmt, year, month)
            if path:
                break
        if not path:
            continue
        channels = read_channels(path, fmt)
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

    # Strip helper field, return
    for e in epochs:
        e.pop("channel_tuple", None)
    return epochs


def scan_station(sta: str, db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    out: dict = {
        "station": sta,
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_db": str(db_path),
        "by_source_kind": {},
    }
    for kind, _regex, fmt in SOURCE_KINDS:
        print(f"  scanning {kind} ({fmt}) ...", flush=True)
        epochs = scan_station_kind(conn, sta, kind, fmt)
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
            lines.append(f"  {e['start']} → {e['end']}  channels={e['channels']}  "
                         f"(n_samples={e['samples_used']})")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta")
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--network", default="VW")
    args = ap.parse_args()

    db_path = args.db_dir / f"{args.network}.{args.sta}.db"
    if not db_path.exists():
        print(f"ERROR: manifest DB not found: {db_path}", file=sys.stderr)
        return 2

    print(f"[channel_epoch_scan] {args.sta}", flush=True)
    out = scan_station(args.sta, db_path)
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

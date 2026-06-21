#!/usr/bin/env python3
"""probe_unknowns.py — two diagnostic probes for the 2026-06-21 findings.

PROBE A — underscore-tele co-existence patterns
  For every station with underscore-tele files in its manifest, compute
  per-day source-kind histograms and bucket the days by which combination
  of (disk, tele_ss, tele_noss, underscore_tele, perchan) is present.
  Tells us whether underscore-tele is a sole source path (must be adopted),
  a duplicate of an existing variant (can be ignored), or a 4th independent
  stream.

PROBE B — RT130 + Minimus LT silent-drop audit
  For LOYU/WILU/TRPU/MOSU/SGWU/DDBE/DDWB/SCM2, sample LT day-files from
  representative dates in each recorder-config era (using _epochs data)
  and report trace count + sample coverage per channel. Tells us whether
  scan 1 silently dropped channels because we never had a grammar for
  their per-channel-mseed file pattern.

Output:
  metadata/source_stats/_probes/<STA>.json   per station
  metadata/source_stats/_probes/_network_summary.{json,txt}
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
import warnings
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
EPOCHS_DIR = Path(__file__).resolve().parent.parent / "metadata" / "source_stats" / "_epochs"
DISC_DIR = Path(__file__).resolve().parent.parent / "metadata" / "source_stats" / "_discovery"
OUT_DIR = Path(__file__).resolve().parent.parent / "metadata" / "source_stats" / "_probes"
LT_ROOT = Path("/mnt/seiscomp_archive")

RT130_AND_MINIMUS = ["LOYU", "WILU", "TRPU", "MOSU", "SGWU",
                     "DDBE", "DDWB", "SCM2"]


# --------------------------------------------------------------------------
# Extended classifier — includes the underscore-tele + RT130 per-channel
# patterns the discovery surfaced.
# --------------------------------------------------------------------------

PATTERNS = [
    # Original documented patterns
    ("disk_suds", re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_\d{2}_\w+\.dmx(\.gz)?$")),
    ("tele_ss_suds", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.dmx(\.gz)?$")),
    ("tele_noss_suds", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.dmx(\.gz)?$")),
    ("disk_mseed", re.compile(r"^\d{8}_\d{4}_\w+\.ms\.zip$")),
    ("tele_ss_mseed", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.ms\.zip$")),
    ("tele_noss_mseed", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ms\.zip$")),
    # NEW: underscore-tele variant ("YYYY-MM-DD HHMM_STA.ms.zip")
    ("underscore_tele_mseed",
     re.compile(r"^\d{4}-\d{2}-\d{2} \d{4}_\w+\.ms\.zip$")),
    # NEW: RT130 per-channel sequence ("YYYY-MM-DD HHMM SS STA_NNN.mseed")
    ("rt130_perchan_seq_mseed",
     re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+_\d{3}\.mseed$")),
    ("rt130_perchan_seq_mseed_zip",
     re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+_\d{3}\.mseed\.zip$")),
    # NEW: RT130-style per-channel with channel code ("YYYY-MM-DD HHMM SS STA_HH2.mseed.zip")
    ("rt130_perchan_chan_mseed_zip",
     re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+_[A-Z][A-Z0-9]\d?\.mseed\.zip$")),
    # NEW: Generic per-channel mseed stubs (DLZ/DLE/DLN era)
    ("perchan_mseed_zip", re.compile(r"^.+_[A-Z]{3}\.mseed\.zip$")),
    ("perchan_mseed", re.compile(r"^.+_[A-Z]{3}\.mseed$")),
    # NEW: triggered Gecko
    ("trig_mseed", re.compile(r"^\d{8}_\d{4}_\w+\.trig\.ms\.zip$")),
    # NEW: hourly aggregate
    ("hourly_mseed", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}hr \w+\.ms\.zip$")),
    # WNRO timing-correction artefacts
    ("wnro_suds", re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.wrno\.dmx(\.gz)?$")),
    # Triggered EchoPro
    ("trig_suds", re.compile(r"^.+\.(trig|\d+(\.trig)?)\.dmx(\.gz)?$")),
    # Kelunjimeta
    ("kelunjimeta_ss", re.compile(r"^.+\.ss$")),
]


def classify_extended(name: str) -> str:
    for pat_name, regex in PATTERNS:
        if regex.match(name):
            return pat_name
    return "UNMATCHED"


# --------------------------------------------------------------------------
# Probe A: underscore-tele co-existence patterns
# --------------------------------------------------------------------------

def probe_a_station(sta: str, db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT path, dir_year, dir_month, dir_day "
        "FROM files WHERE role != 'metadata' AND exclude_reason IS NULL"
    ).fetchall()
    conn.close()

    days: dict = defaultdict(lambda: Counter())
    for path, yr, mo, dy in rows:
        name = path.split("/")[-1] if path else ""
        kind = classify_extended(name)
        days[(yr, mo, dy)][kind] += 1

    # Only days with underscore_tele_mseed present
    rel_days = [(d, c) for d, c in days.items()
                if c.get("underscore_tele_mseed", 0) > 0]
    if not rel_days:
        return {"station": sta, "n_days_with_underscore_tele": 0,
                "underscore_tele_total_files": 0,
                "co_occurrence_patterns": {}}

    # Bucket each day by which kinds are present
    pat_counts: Counter = Counter()
    pat_underscore_counts: dict = defaultdict(int)
    pat_other_counts: dict = defaultdict(lambda: Counter())
    track_kinds = ("disk_mseed", "disk_suds",
                   "tele_ss_mseed", "tele_noss_mseed",
                   "underscore_tele_mseed",
                   "tele_ss_suds", "tele_noss_suds",
                   "rt130_perchan_seq_mseed", "rt130_perchan_seq_mseed_zip",
                   "rt130_perchan_chan_mseed_zip",
                   "perchan_mseed_zip", "perchan_mseed",
                   "trig_mseed", "hourly_mseed", "wnro_suds",
                   "trig_suds")

    total_underscore_files = 0
    for day, counts in rel_days:
        present = tuple(sorted(k for k in track_kinds if counts.get(k, 0) > 0))
        pat_counts[present] += 1
        pat_underscore_counts[present] += counts.get("underscore_tele_mseed", 0)
        for k in present:
            pat_other_counts[present][k] += counts.get(k, 0)
        total_underscore_files += counts.get("underscore_tele_mseed", 0)

    # Format for output
    co_patterns = []
    for pattern, count in pat_counts.most_common(20):
        co_patterns.append({
            "kinds_present": list(pattern),
            "n_days": count,
            "underscore_tele_files_in_this_pattern": pat_underscore_counts[pattern],
            "median_kind_counts": {
                k: round(pat_other_counts[pattern][k] / count)
                for k in pattern
            },
        })

    return {
        "station": sta,
        "n_days_with_underscore_tele": len(rel_days),
        "underscore_tele_total_files": total_underscore_files,
        "co_occurrence_patterns": co_patterns,
    }


# --------------------------------------------------------------------------
# Probe B: LT audit for RT130 + Minimus stations
# --------------------------------------------------------------------------

def doy_of(year: int, month: int, day: int) -> int:
    return date(year, month, day).timetuple().tm_yday


def probe_b_station(sta: str, epoch_data: dict) -> dict:
    warnings.filterwarnings("ignore")
    from obspy import read

    samples = []
    # For each source kind in epoch data, pick representative dates
    kinds = epoch_data.get("by_source_kind", {})
    if not kinds:
        return {"station": sta, "samples": [], "note": "no epoch data"}

    # Pick years from any disk epoch (we want to audit what LT has) — fall back
    # to whatever kinds are in the epoch file
    test_years: set = set()
    for kind, epochs in kinds.items():
        for era in epochs:
            try:
                y0 = int(era["start"][:4])
                y1 = int(era["end"][:4])
                # Pick start, middle, end of era
                test_years.add(y0)
                test_years.add((y0 + y1) // 2)
                test_years.add(y1)
            except (KeyError, ValueError):
                pass
    test_years = sorted(y for y in test_years if 2012 <= y <= 2025)
    if not test_years:
        return {"station": sta, "samples": [],
                "note": "no in-scope years in epochs"}

    # For each test year, sample a mid-year day; try common channel codes
    candidate_channels = ["CHZ", "CHN", "CHE",
                          "HHZ", "HHN", "HHE",
                          "DHZ", "DHN", "DHE",
                          "EHZ", "EHN", "EHE"]
    for year in test_years:
        doy = doy_of(year, 7, 15)  # mid-July
        for chan in candidate_channels:
            f = (LT_ROOT / f"{year:04d}" / "VW" / sta / f"{chan}.D" /
                 f"VW.{sta}.00.{chan}.D.{year:04d}.{doy:03d}")
            if not f.exists():
                continue
            try:
                st = read(str(f), headonly=True)
                npts = sum(tr.stats.npts for tr in st)
                rate = st[0].stats.sampling_rate if len(st) else None
                expected = int(round(86400 * rate)) if rate else None
                pct = round(100 * npts / expected, 2) if expected else None
                samples.append({
                    "year": year, "doy": doy, "chan": chan,
                    "n_traces": len(st), "npts": npts,
                    "sample_rate": rate, "pct_of_full_day": pct,
                })
            except Exception as e:
                samples.append({
                    "year": year, "doy": doy, "chan": chan,
                    "error": f"{type(e).__name__}: {e}",
                })
    return {"station": sta, "samples": samples}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--network", default="VW")
    ap.add_argument("--probe", choices=["a", "b", "both"], default="both")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    network_summary = {
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "probe_a": {},
        "probe_b": {},
    }

    if args.probe in ("a", "both"):
        # Read discovery JSONs to find which stations have unmatched > 1000
        unmatched_per_station = {}
        for f in sorted(DISC_DIR.glob("*.json")):
            d = json.loads(f.read_text())
            for g in d.get("filename_grammar", []):
                if g["pattern"] == "UNMATCHED":
                    unmatched_per_station[d["station"]] = g["count"]
                    break
        candidate_stations = [s for s, n in unmatched_per_station.items() if n > 1000]
        print(f"[probe A] {len(candidate_stations)} stations with > 1000 unmatched",
              flush=True)

        a_results = {}
        for sta in sorted(candidate_stations):
            db_path = args.db_dir / f"{args.network}.{sta}.db"
            if not db_path.exists():
                continue
            print(f"  probe A on {sta}...", flush=True)
            r = probe_a_station(sta, db_path)
            a_results[sta] = r
            (args.out_dir / f"{sta}_probe_a.json").write_text(
                json.dumps(r, indent=2, sort_keys=True))
        network_summary["probe_a"] = {
            "stations_scanned": list(a_results.keys()),
            "stations_with_underscore_tele": [
                sta for sta, r in a_results.items()
                if r["n_days_with_underscore_tele"] > 0
            ],
        }

    if args.probe in ("b", "both"):
        print(f"[probe B] LT audit for {len(RT130_AND_MINIMUS)} stations",
              flush=True)
        b_results = {}
        for sta in RT130_AND_MINIMUS:
            epoch_file = EPOCHS_DIR / f"{sta}.json"
            if not epoch_file.exists():
                print(f"  {sta}: no epoch file, skipping", flush=True)
                continue
            epoch_data = json.loads(epoch_file.read_text())
            print(f"  probe B on {sta}...", flush=True)
            r = probe_b_station(sta, epoch_data)
            b_results[sta] = r
            (args.out_dir / f"{sta}_probe_b.json").write_text(
                json.dumps(r, indent=2, sort_keys=True))
        network_summary["probe_b"] = {
            "stations_scanned": list(b_results.keys()),
        }

    summary_path = args.out_dir / "_network_summary.json"
    summary_path.write_text(json.dumps(network_summary, indent=2, sort_keys=True))
    print(f"\n[wrote] {summary_path}", flush=True)

    # Format text summary
    lines = []
    lines.append(f"# probe_unknowns summary  ({network_summary['generated_at_utc']})")
    lines.append("")
    if args.probe in ("a", "both"):
        lines.append("## Probe A — underscore-tele co-existence")
        for sta in network_summary["probe_a"]["stations_with_underscore_tele"]:
            r = a_results[sta]
            lines.append(f"  {sta}: {r['n_days_with_underscore_tele']:5d} days, "
                         f"{r['underscore_tele_total_files']:10,} underscore-tele files")
            for p in r["co_occurrence_patterns"][:5]:
                kinds_str = ", ".join(p["kinds_present"])
                u = p["underscore_tele_files_in_this_pattern"]
                lines.append(f"    {p['n_days']:4d} days  "
                             f"u_tele_files={u:7,}   kinds: {kinds_str}")
        lines.append("")
    if args.probe in ("b", "both"):
        lines.append("## Probe B — RT130/Minimus LT audit")
        for sta in network_summary["probe_b"]["stations_scanned"]:
            r = b_results[sta]
            lines.append(f"  {sta}:")
            for s in r["samples"]:
                if "error" in s:
                    continue
                lines.append(f"    {s['year']} doy={s['doy']}  "
                             f"{s['chan']:4s}  n_traces={s['n_traces']:5d}  "
                             f"npts={s['npts']:10d}  "
                             f"{s['pct_of_full_day']:.2f}% of day  "
                             f"@ {s['sample_rate']} Hz")
    summary_txt_path = args.out_dir / "_network_summary.txt"
    summary_txt_path.write_text("\n".join(lines) + "\n")
    print(f"[wrote] {summary_txt_path}", flush=True)
    print()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

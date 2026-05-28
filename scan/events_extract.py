#!/usr/bin/env python3
"""Event-driven QA: convert + extract ±2.5 min event windows for all VW stations.

For each earthquake in the input CSV:
  1. Determine event_date and ±2.5 min UTC window from origin_time
  2. Run Phase 3 conversion across all VW per-station DBs for that date,
     writing day-SDS to a per-event temp dir
  3. For each day-SDS produced, slice the event window and write to
     events/<event_id>/<station>/ as event-only mseed
  4. Record two completeness measures per event:
       - day-level: which stations had any data for the date
       - event-window-level: which had non-empty data in the 5-min window
     Flag stations with day data but no event-window data (recorder transition).
  5. Delete the temp day-SDS (only the event windows are kept).

Run (after preliminary run finishes):
  python3 scan/events_extract.py \
      --events-csv /mnt/seiscomp_staging/qa_check/events/earthquakes_export.csv \
      --station-dbs /home/.../station_dbs \
      --plans /tmp/plans_vw \
      --registry .../station_registry.yaml \
      --out /mnt/seiscomp_staging/qa_check/events \
      --pool-size 4 --per-station-workers 4
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_VENV_PY = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3"
WINDOW_SEC = 150  # ±2.5 min around origin → 5 min total


def parse_events(csv_path):
    """Yield (event_id, origin_utc, description, magnitude) per event."""
    with open(csv_path) as f:
        # First line is the "Search criteria" comment; second is header
        first = f.readline()
        if not first.startswith("FID,") and not first.startswith("\"Search"):
            f.seek(0)
        # Skip until header row
        for line in f:
            if line.startswith("FID,"):
                header = line.strip().split(",")
                break
        else:
            raise ValueError("CSV has no FID, header row")
        reader = csv.DictReader(f, fieldnames=header)
        for row in reader:
            try:
                origin = datetime.fromisoformat(row["origin_time"].replace("Z", "+00:00"))
                if origin.tzinfo is None:
                    origin = origin.replace(tzinfo=timezone.utc)
                yield {
                    "event_id": row["event_id"],
                    "origin_utc": origin,
                    "description": row.get("description", ""),
                    "magnitude": row.get("preferred_magnitude", ""),
                    "magnitude_type": row.get("preferred_magnitude_type", ""),
                    "latitude": row.get("latitude", ""),
                    "longitude": row.get("longitude", ""),
                    "depth": row.get("depth", ""),
                }
            except Exception as e:
                print(f"  CSV row parse error: {e} | row={row.get('event_id', '?')}", flush=True)


def run_phase3_for_date(station_dbs, plans, registry, staging_sds, event_date,
                        pool_size, per_station_workers, python_bin):
    """Run run_phase3_pool.py for ONE day across all VW stations."""
    cmd = [
        python_bin, "-u",
        os.path.join(HERE, "run_phase3_pool.py"),
        "--station-dbs", station_dbs,
        "--plans", plans,
        "--registry", registry,
        "--staging-sds", staging_sds,
        "--start-date", event_date,
        "--end-date", event_date,
        "--pool-size", str(pool_size),
        "--per-station-workers", str(per_station_workers),
        "--commit",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def trim_and_record(staging_sds, event_id, origin_utc, out_dir):
    """For each SDS file written by run_phase3_for_date, extract ±2.5 min around
    origin_utc and write to out_dir/<event_id>/VW.<sta>.00.<chan>.mseed.
    Returns per-station coverage dict."""
    from obspy import read, UTCDateTime
    win_start = UTCDateTime(origin_utc) - WINDOW_SEC
    win_end = UTCDateTime(origin_utc) + WINDOW_SEC

    event_dir = os.path.join(out_dir, event_id)
    os.makedirs(event_dir, exist_ok=True)

    coverage = {}
    for root, _, files in os.walk(staging_sds):
        for f in files:
            if "VW." not in f or ".D." not in f:
                continue
            path = os.path.join(root, f)
            parts = f.split(".")
            sta = parts[1]
            cha = parts[3]
            try:
                st = read(path)
            except Exception as e:
                coverage.setdefault(sta, {})[cha] = {"error": str(e)}
                continue

            # Day-level: total npts in the whole day file
            day_npts = sum(tr.stats.npts for tr in st)
            day_rate = st[0].stats.sampling_rate

            # Event-window slice
            try:
                ev = st.slice(win_start, win_end)
            except Exception:
                ev = None

            if ev and len(ev) > 0:
                ev_npts = sum(tr.stats.npts for tr in ev)
                ev_traces = len(ev)
                expected_ev = int(day_rate * 2 * WINDOW_SEC)
                ev_coverage_pct = round(100 * ev_npts / expected_ev, 1) if expected_ev else 0
                ev_path = os.path.join(event_dir, f"VW.{sta}.00.{cha}.event.mseed")
                ev.write(ev_path, format="MSEED")
            else:
                ev_npts = 0
                ev_traces = 0
                ev_coverage_pct = 0.0

            entry = coverage.setdefault(sta, {})
            entry[cha] = {
                "day_npts": day_npts,
                "event_npts": ev_npts,
                "event_traces": ev_traces,
                "event_coverage_pct": ev_coverage_pct,
                "rate_hz": day_rate,
            }
    return coverage


def write_event_summary(out_dir, event, coverage, all_stations):
    """Per-event SUMMARY.md with day-level and event-window-level completeness."""
    event_id = event["event_id"]
    event_dir = os.path.join(out_dir, event_id)
    os.makedirs(event_dir, exist_ok=True)

    stations_with_day_data = set(coverage)
    stations_with_event_data = {
        s for s, chans in coverage.items()
        if any(c.get("event_npts", 0) > 0 for c in chans.values())
    }
    flagged_no_event = stations_with_day_data - stations_with_event_data

    with open(os.path.join(event_dir, "SUMMARY.md"), "w") as f:
        f.write(f"# Event {event_id}\n\n")
        f.write(f"- **Description**: {event.get('description','')}\n")
        f.write(f"- **Origin (UTC)**: {event['origin_utc'].isoformat()}\n")
        f.write(f"- **Magnitude**: {event.get('magnitude','')} {event.get('magnitude_type','')}\n")
        f.write(f"- **Location**: {event.get('latitude','')}, {event.get('longitude','')} "
                f"depth={event.get('depth','')}\n")
        f.write(f"- **Window**: ±{WINDOW_SEC} s ({2*WINDOW_SEC}s total) around origin\n\n")
        f.write(f"## Completeness\n\n")
        f.write(f"- Total VW stations in scope: **{len(all_stations)}**\n")
        f.write(f"- Stations with day-level data: **{len(stations_with_day_data)}**\n")
        f.write(f"- Stations with event-window data: **{len(stations_with_event_data)}**\n")
        f.write(f"- Stations flagged (had day data, no event-window data): "
                f"**{len(flagged_no_event)}** — {sorted(flagged_no_event)}\n\n")
        f.write(f"## Per-station detail\n\n")
        f.write(f"| Station | Channels | Day npts | Event npts | Event coverage |\n")
        f.write(f"|---|---|---:|---:|---:|\n")
        for sta in sorted(stations_with_day_data):
            for cha in sorted(coverage[sta]):
                e = coverage[sta][cha]
                if "error" in e:
                    f.write(f"| {sta} | {cha} | ERROR | — | {e['error'][:60]} |\n")
                    continue
                f.write(f"| {sta} | {cha} | {e['day_npts']:,} | {e['event_npts']:,} | "
                        f"{e['event_coverage_pct']}% |\n")

    # Also dump machine-readable
    with open(os.path.join(event_dir, "coverage.json"), "w") as f:
        json.dump({
            "event_id": event_id,
            "origin_utc": event["origin_utc"].isoformat(),
            "description": event.get("description", ""),
            "magnitude": event.get("magnitude", ""),
            "window_sec": WINDOW_SEC,
            "stations_in_scope": sorted(all_stations),
            "stations_with_day_data": sorted(stations_with_day_data),
            "stations_with_event_data": sorted(stations_with_event_data),
            "flagged_day_data_no_event": sorted(flagged_no_event),
            "coverage": coverage,
        }, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events-csv", required=True)
    ap.add_argument("--station-dbs", required=True)
    ap.add_argument("--plans", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out", required=True, help="qa_check/events output dir")
    ap.add_argument("--temp-staging", default="/tmp/event_work",
                    help="ephemeral staging for the per-day SDS (deleted after trim)")
    ap.add_argument("--pool-size", type=int, default=4)
    ap.add_argument("--per-station-workers", type=int, default=4)
    ap.add_argument("--python", default=DEFAULT_VENV_PY)
    ap.add_argument("--limit-events", type=int, default=0,
                    help="stop after this many events (for testing); 0=all")
    args = ap.parse_args()

    # Discover all-VW stations from station_dbs
    all_stations = sorted({
        f.split(".")[1] for f in os.listdir(args.station_dbs)
        if f.startswith("VW.") and f.endswith(".db")
    })
    print(f"[events] {len(all_stations)} VW stations in scope")

    events = list(parse_events(args.events_csv))
    print(f"[events] {len(events)} events to process")
    if args.limit_events:
        events = events[: args.limit_events]
        print(f"[events] limited to first {len(events)}")

    os.makedirs(args.out, exist_ok=True)
    grand_summary = []

    for i, ev in enumerate(events, 1):
        eid = ev["event_id"]
        origin = ev["origin_utc"]
        event_date = origin.date().isoformat()
        t0 = time.time()
        print(f"\n[{i}/{len(events)}] {eid} {event_date} mag={ev['magnitude']} "
              f"{ev['description']}", flush=True)

        # Per-event ephemeral staging
        ev_stage = os.path.join(args.temp_staging, eid)
        if os.path.exists(ev_stage):
            shutil.rmtree(ev_stage, ignore_errors=True)
        os.makedirs(ev_stage, exist_ok=True)

        try:
            rc, out, err = run_phase3_for_date(
                args.station_dbs, args.plans, args.registry, ev_stage,
                event_date, args.pool_size, args.per_station_workers, args.python,
            )
            if rc != 0:
                print(f"  Phase3 returned rc={rc}; stderr tail:\n{err[-500:]}", flush=True)

            coverage = trim_and_record(ev_stage, eid, origin, args.out)
            write_event_summary(args.out, ev, coverage, all_stations)
            n_day = len(coverage)
            n_evt = sum(1 for s, chans in coverage.items()
                        if any(c.get("event_npts", 0) > 0 for c in chans.values()))
            elapsed = time.time() - t0
            print(f"  done in {elapsed:.0f}s: {n_day} stations with day data, "
                  f"{n_evt} with event-window data", flush=True)
            grand_summary.append({
                "event_id": eid, "date": event_date,
                "n_day": n_day, "n_event": n_evt, "elapsed_s": round(elapsed, 1),
            })
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}", flush=True)
            grand_summary.append({
                "event_id": eid, "date": event_date, "error": str(e),
            })
        finally:
            shutil.rmtree(ev_stage, ignore_errors=True)

    # Write grand summary
    with open(os.path.join(args.out, "ALL_EVENTS_SUMMARY.md"), "w") as f:
        f.write(f"# Events QA — all {len(events)} events\n\n")
        f.write(f"| # | event_id | date | day-stations | event-stations | wallclock_s |\n")
        f.write(f"|---:|---|---|---:|---:|---:|\n")
        for i, e in enumerate(grand_summary, 1):
            if "error" in e:
                f.write(f"| {i} | {e['event_id']} | {e['date']} | ERROR | ERROR | {e['error'][:60]} |\n")
            else:
                f.write(f"| {i} | {e['event_id']} | {e['date']} | {e['n_day']} | "
                        f"{e['n_event']} | {e['elapsed_s']} |\n")
    with open(os.path.join(args.out, "ALL_EVENTS_SUMMARY.json"), "w") as f:
        json.dump(grand_summary, f, indent=2)

    print(f"\n[events] DONE. Summary: {args.out}/ALL_EVENTS_SUMMARY.md")


if __name__ == "__main__":
    sys.exit(main())

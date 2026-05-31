#!/usr/bin/env python3
"""Phase 2b day-plan generator.

Reads a Level-1 manifest DB + station_registry.yaml, runs the Phase 2b classifier
per station-day, groups consecutive same-recorder days into epochs, and emits one
plan YAML per station at plans/<NET>.<STA>.plan.yaml.

Each plan tells Phase 3 conversion:
  - station identity (network, code, location)
  - epochs (one per recorder-class run), with day counts + class breakdown
  - flagged days needing review (anything outside CLEAN_CATEGORIES) — descriptive,
    not a skip signal (see [[feedback-convert-what-is-on-disc]] in agent memory)
  - top-level status: ok | defer_conversion
      'defer_conversion' = registry-annotated recorder type whose EqServer
        presence is a known-misleading partial (PiesMo HHZ-only stub).
      'ok'              = everything else. The pipeline's job is to convert
        what's on disc; recorder restarts / partial days / failing-recorder
        stretches / disk-vs-tele disagreement are OPERATIONAL REALITY, not
        skip signals. Per-day classifications are descriptive labels for QA;
        they do NOT gate conversion.

The per-day file-use list is NOT materialised here — it stays a manifest query at
conversion time (avoids enormous YAML; the manifest is the source of truth).

Run:
  python3 scan/plan_generator.py <manifest.db> --registry metadata/station_registry.yaml --out plans/

Requires pyyaml.
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from collections import Counter
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_manifest import (  # noqa: E402
    classify,
    CLEAN_CATEGORIES,
    PER_DAY_SQL,
    MINIMUS_STATIONS_DEFAULT,
)

MAX_FLAGGED_IN_YAML = 200

# Recorders whose EqServer presence we deliberately DO NOT convert in this pipeline.
# PiesMo only sends a sporadic HHZ-only telemetry stub to EqServer (~250-300 files/day);
# the bulk 3-component data reaches SeisComP via separate paths (direct seedlink /
# SD-card via disk_to_sds). Converting the EqServer stub would create misleading
# partial records, so PiesMo stations are flagged defer_conversion.
DEFER_CONVERSION_RECORDERS = {"piesmo"}


def load_registry(path):
    """{station: registry_entry_dict} for stations with include: true."""
    import yaml
    reg = yaml.safe_load(open(path))
    return {s: v for s, v in reg.items()
            if isinstance(v, dict) and v.get("include") is True}


def manifest_recorder(r):
    """Manifest-inferred recorder from row's n_*_ok counts."""
    rec_counts = {"echopro": r["n_echopro_ok"], "gecko": r["n_gecko_ok"], "mseed": r["n_mseed_ok"]}
    if not any(rec_counts.values()):
        return "unknown"
    return max(rec_counts, key=rec_counts.get)


def resolve_recorder_for_day(manifest_inferred, registry_entry):
    """Reconcile manifest inference against registry annotation.
    - Registry single-value override wins (authoritative annotation).
    - Registry multi-value or absent: fall back to manifest inference.
    """
    if registry_entry and registry_entry.get("recorder_types"):
        rt = registry_entry["recorder_types"]
        if isinstance(rt, list) and len(rt) == 1:
            return rt[0]
    return manifest_inferred


def gather_station_days(conn, station, registry_entry, minimus_set):
    sql = PER_DAY_SQL.replace(
        "FROM files\nWHERE dir_year > 0",
        "FROM files\nWHERE dir_year > 0 AND station = ?",
    )
    rows = list(conn.execute(sql, (station,)))
    out = []
    for r in rows:
        sc = "minimus" if r["station"] in minimus_set else None
        cls = classify(r, station_class=sc)
        rec_m = manifest_recorder(r)
        rec_r = resolve_recorder_for_day(rec_m, registry_entry)
        out.append({
            "date": date(r["dir_year"], r["dir_month"], r["dir_day"]).isoformat(),
            "classification": cls,
            "recorder": rec_r,
            "recorder_inferred": rec_m,
            "n_disk_ok": r["n_disk_ok"],
            "n_tele_ok": r["n_tele_ok"],
            "n_single_chan": r["n_single_chan"],
        })
    out.sort(key=lambda d: d["date"])
    return out


def group_epochs(daily_rows):
    """Group consecutive same-recorder days into epochs.
    Boundary fires when recorder changes."""
    epochs = []
    cur = None
    for d in daily_rows:
        if cur is None or cur["recorder"] != d["recorder"]:
            if cur is not None:
                epochs.append(cur)
            cur = {"start": d["date"], "end": d["date"], "recorder": d["recorder"],
                   "days": 0, "classifications": Counter()}
        cur["end"] = d["date"]
        cur["days"] += 1
        cur["classifications"][d["classification"]] += 1
    if cur is not None:
        epochs.append(cur)
    return epochs


def build_plan(station, registry_entry, daily_rows):
    epochs = group_epochs(daily_rows)
    flagged = [
        {"date": d["date"], "classification": d["classification"],
         "n_disk_ok": d["n_disk_ok"], "n_tele_ok": d["n_tele_ok"]}
        for d in daily_rows if d["classification"] not in CLEAN_CATEGORIES
    ]
    n_total = len(daily_rows)
    n_clean = sum(1 for d in daily_rows if d["classification"] in CLEAN_CATEGORIES)
    # defer_conversion gate: registry-annotated recorder type whose EqServer
    # presence is a known-misleading partial (PiesMo HHZ-only stub). This is the
    # ONLY thing that gates a station out of the sweep. Per-day classifications
    # are descriptive labels for QA, NOT skip signals — the pipeline converts
    # what's on disc. See [[feedback-convert-what-is-on-disc]].
    reg_rt = (registry_entry.get("recorder_types") if registry_entry else None) or []
    defer = any(rt in DEFER_CONVERSION_RECORDERS for rt in reg_rt)
    status = "defer_conversion" if defer else "ok"
    # target_location from registry (per-station enforcement). Defaults to "00"
    # for the VW/VX networks where the operator convention is uniform "00".
    # Source mseed often has empty location (Gecko/Reftek/Minimus all write '');
    # the driver REPLACES the empty source location with this value.
    default_loc = "00"
    target_location = (registry_entry.get("target_location")
                       if registry_entry else None) or default_loc
    plan = {
        "station": station,
        "network": registry_entry.get("target_network") if registry_entry else None,
        "location": target_location,
        "status": status,
        "summary": {
            "days_total": n_total,
            "days_clean": n_clean,
            "days_flagged": len(flagged),
            "pct_clean": round(100 * n_clean / n_total, 1) if n_total else 0.0,
        },
        "registry_recorder_types": registry_entry.get("recorder_types") if registry_entry else None,
        "epochs": [
            {
                "id": i + 1,
                "start": ep["start"], "end": ep["end"],
                "recorder": ep["recorder"],
                "days": ep["days"],
                "classifications": dict(ep["classifications"]),
            }
            for i, ep in enumerate(epochs)
        ],
        "flagged_days": flagged[:MAX_FLAGGED_IN_YAML],
    }
    if len(flagged) > MAX_FLAGGED_IN_YAML:
        plan["flagged_days_truncated"] = len(flagged) - MAX_FLAGGED_IN_YAML
    if defer:
        plan["defer_reason"] = (
            "registry recorder_types includes one of "
            f"{sorted(DEFER_CONVERSION_RECORDERS)}; EqServer presence is a partial "
            "stub, full data reaches SeisComP via a separate path."
        )
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="Level-1 manifest SQLite path")
    ap.add_argument("--registry", required=True,
                    help="path to metadata/station_registry.yaml")
    ap.add_argument("--out", default="plans",
                    help="output directory for <NET>.<STA>.plan.yaml files")
    ap.add_argument("--minimus-stations",
                    default=",".join(sorted(MINIMUS_STATIONS_DEFAULT)))
    ap.add_argument("--stations", default="",
                    help="comma-separated subset (default: all include:true with data)")
    args = ap.parse_args()

    import yaml
    minimus_set = {s.strip() for s in args.minimus_stations.split(",") if s.strip()}
    registry = load_registry(args.registry)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    # Ensure helpful index for per-station day queries (one-time cost ~ seconds)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station_dir ON files(station, dir_year, dir_month, dir_day)")
    conn.commit()
    # All stations in the manifest — use the station index, no WHERE filter
    present = {r[0] for r in conn.execute("SELECT DISTINCT station FROM files")}
    if args.stations:
        target_stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    else:
        target_stations = sorted(present & set(registry.keys()))

    os.makedirs(args.out, exist_ok=True)
    print(f"[plan-gen] {len(target_stations)} stations to plan from {args.db}")
    by_status = Counter()

    for sta in target_stations:
        reg_entry = registry.get(sta)
        daily = gather_station_days(conn, sta, reg_entry, minimus_set)
        if not daily:
            print(f"  {sta:6} no days in manifest, skipping"); continue
        plan = build_plan(sta, reg_entry, daily)
        net = plan["network"] or "UNK"
        path = os.path.join(args.out, f"{net}.{sta}.plan.yaml")
        with open(path, "w") as f:
            yaml.safe_dump(plan, f, sort_keys=False, default_flow_style=False)
        by_status[plan["status"]] += 1
        print(f"  {net}.{sta:6} {plan['status']:13} epochs={len(plan['epochs']):>2} "
              f"days={plan['summary']['days_total']:>5} "
              f"clean={plan['summary']['days_clean']:>5} ({plan['summary']['pct_clean']:5.1f}%) "
              f"flagged={plan['summary']['days_flagged']:>4}")

    print(f"\n[plan-gen] wrote plan YAMLs to {args.out}/")
    print(f"  status breakdown: {dict(by_status)}")


if __name__ == "__main__":
    sys.exit(main())

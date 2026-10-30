#!/usr/bin/env python3
"""make_du_plans — DU conversion plans from the operator's naming rule
(ruling 2026-10-29):

  1. Station already archived on the SeisComP server  -> server codes
     verbatim (naming: server_verbatim; applied to every source format),
     per epoch-year, nearest archived year when the epoch year itself is
     not on the server.
  2. Not on the server, native miniSEED               -> the files' own
     header names (naming: passthrough, location: passthrough).
  3. Not on the server, SUDS                          -> SEED short-period
     default. NOT generated here: needs a sample rate per epoch; such
     stations are reported as unresolved for a follow-up pass.

Inputs: --census JSON {STA: [[year, recorder_type, n_days], ...]} (from
the Level-1 station DBs), --lt-codes JSON (metadata/derived/
du_lt_codes.json: {STA: {year: {chan: [loc, ...]}}}).

Plans are written with status: ok — the operator approves them before
any production run (they are committed, reviewable artifacts).
"""
import argparse
import json
from pathlib import Path

import yaml

MSEED = {"mseed", "gecko", "minimus"}


def epoch_recorder(rows_for_year):
    """Majority recorder for a year -> plan recorder label."""
    best = max(rows_for_year, key=lambda r: r[2])
    return "echopro" if best[1] == "echopro" else "gecko"


def lt_codes_for(lt, year):
    """Server codes for `year`, else the nearest archived year."""
    if not lt:
        return None, None
    if str(year) in lt:
        return lt[str(year)], year
    near = min(lt, key=lambda y: abs(int(y) - year))
    return lt[near], int(near)


def seed_sp_band(rate):
    """SEED short-period band letter by sample rate (operator default when
    the instrument class is unknown)."""
    if rate is None:
        return None
    if rate >= 1000:
        return "G"
    if rate >= 250:
        return "D"
    if rate >= 80:
        return "E"
    if rate >= 10:
        return "S"
    return None


def make_plan(sta, rows, lt, facts=None):
    years = sorted({r[0] for r in rows})
    total = sum(r[2] for r in rows) or 1
    mseed_frac = sum(r[2] for r in rows if r[1] in MSEED) / total
    epochs, notes, unresolved = [], [], []
    if lt:
        naming = "server_verbatim"
    elif mseed_frac >= 0.95:
        naming = "passthrough"
    else:
        # Not on the server, SUDS (wholly or partly): SEED short-period
        # default from the sample rate measured in the station's own files
        # (metadata/derived/du_station_facts.json). miniSEED files of the
        # same station keep their header names (naming passthrough; the
        # SEED map applies to SUDS only).
        f = (facts or {}).get(sta) or {}
        rates = f.get("rates") or []
        band = seed_sp_band(rates[0]) if (f.get("format") == "suds"
                                          and len(rates) == 1) else None
        if band is None:
            return None, (f"{sta}: not on server, SUDS — no single measured "
                          f"SUDS sample rate (facts: format={f.get('format')}, "
                          f"rates={rates}); SEED default cannot be derived")
        naming = "passthrough"
        seed_targets = {o: f"DU.{sta}.00.{band}H{o}" for o in "ZNE"}
    locs = []
    for i, y in enumerate(years, 1):
        yr_rows = [r for r in rows if r[0] == y]
        ep = {"id": i, "start": f"{y}-01-01", "end": f"{y}-12-31",
              "recorder": epoch_recorder(yr_rows), "days": 0,
              "classifications": {}}
        if naming == "server_verbatim":
            codes, src_year = lt_codes_for(lt, y)
            by_orient = {}
            for chan, loc_list in sorted(codes.items()):
                for loc in loc_list:
                    by_orient.setdefault(chan[-1], []).append((loc, chan))
            chans = []
            for orient, cands in sorted(by_orient.items()):
                if len(cands) > 1:
                    unresolved.append(
                        f"{sta} {y}: orientation {orient} has several server "
                        f"codes {cands} (server year {src_year})")
                    continue
                loc, chan = cands[0]
                locs.append(loc)
                chans.append({"component": orient,
                              "target": f"DU.{sta}.{loc}.{chan}",
                              "basis": f"server_verbatim:{src_year}"})
            ep["channels"] = chans
            if src_year != y:
                notes.append(f"{y}: codes from nearest server year {src_year}")
        if naming == "passthrough" and "seed_targets" in locals():
            ep["channels"] = [{"component": o, "target": t,
                               "basis": f"seed_short_period_default:"
                                        f"{rates[0]:g}sps"}
                              for o, t in sorted(seed_targets.items())]
        epochs.append(ep)
    plan = {
        "station": sta, "network": "DU",
        "location": ("passthrough" if naming == "passthrough"
                     else max(set(locs), key=locs.count) if locs else "00"),
        "naming": naming,
        "status": "needs_review" if unresolved else "ok",
        "epochs": epochs, "flagged_days": [],
        "generated_by": "scan/make_du_plans.py (operator naming rule "
                        "2026-10-29)",
        "notes": notes,
    }
    if unresolved:
        plan["unresolved"] = unresolved
    return plan, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--census", required=True)
    ap.add_argument("--lt-codes", default="metadata/derived/du_lt_codes.json")
    ap.add_argument("--stations", required=True, help="comma-separated")
    ap.add_argument("--out-dir", default="plans/DU")
    ap.add_argument("--facts", default="metadata/derived/du_station_facts.json",
                    help="sample-file facts (format, measured rates)")
    args = ap.parse_args()
    census = json.load(open(args.census))
    lt_all = json.load(open(args.lt_codes))
    facts = json.load(open(args.facts)) if Path(args.facts).exists() else {}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for sta in [s.strip() for s in args.stations.split(",") if s.strip()]:
        rows = census.get(sta)
        if not rows:
            print(f"SKIP {sta}: no census rows")
            continue
        plan, why = make_plan(sta, rows, lt_all.get(sta), facts)
        if plan is None:
            print(f"UNRESOLVED {why}")
            continue
        p = out / f"DU.{sta}.plan.yaml"
        p.write_text(yaml.safe_dump(plan, sort_keys=False))
        print(f"{plan['status']:13s} {p}  naming={plan['naming']} "
              f"location={plan['location']} epochs={len(plan['epochs'])}")
        for u in plan.get("unresolved", []):
            print(f"   UNRESOLVED {u}")


if __name__ == "__main__":
    main()

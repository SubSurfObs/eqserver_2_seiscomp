#!/usr/bin/env python3
"""source_lt_gap_audit.py — find unintentional skips between EqServer
source and the long-term archive.

For every (net, sta, year) in scope, enumerates:

  S = source_days     — date-dirs under EqServer archive (after filtering
                        bogus-date dirs like 1900/1970/1980/1989/1999)
  L = lt_days         — dates that have at least one LT day-file
                        (union across all SDS channels for that sta/year)
  F = plan_flagged    — dates in the plan's flagged_days list (operator-
                        intentional pause-for-review per v3-OptionB
                        classifier)
  E = manifest_empty  — dates manifested with status='no_files' (source
                        legitimately empty — not a conversion loss)

Then for each (sta, year):

  intentional_gap   = (F ∪ E) ∩ S      — properly skipped
  unintentional_gap = S − L − F − E    — REAL GAPS, the bloodhound target

Within unintentional_gap, sub-classify by consulting the run manifest's
per_date_status:

  - status='timeout' or 'error' or 'parse_error': tried-but-failed
  - status='ok' and bytes_written==0: silent zero-output (bug class)
  - date absent from manifest entirely: NEVER ATTEMPTED (recovery script
    or sweep had a too-narrow date range)

The third category is the LOYU 2019 case — source had data, recovery
never tried.

USAGE:
  python3 scan/source_lt_gap_audit.py \\
      --registry metadata/station_registry.yaml \\
      --archive /mnt/eqserver_archive/shared/data/repository/archive \\
      --lt-root /mnt/seiscomp_archive \\
      --queue-dir /mnt/seiscomp_staging/eqserver_sweep \\
      --plans plans/VW \\
      --network VW \\
      --out /tmp/vw_gap_audit.txt
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import yaml


# Bogus-date directories that aren't real station years (per CLAUDE.md).
# 2001 has real data for OUTU so don't blanket-exclude it.
BOGUS_YEARS = {"1900", "1970", "1980", "1989", "1999", "2000", "2002",
               "2004", "2007", "2008", "2009"}


def list_source_days(station_dbs: Path, network: str, sta: str,
                     year: int) -> set[date]:
    """Use the indexed Level-1 station DB to find real source day-dates
    for one (sta, year). Avoids NFS walks — orders of magnitude faster.

    DB schema (per scan/level1.py): table `files` with columns
    file_year, file_month, file_day (filename-derived; authoritative per
    CLAUDE.md), dir_year/dir_month/dir_day (path-derived; less reliable
    due to bogus-date dirs), role, exclude_reason.

    Counts a day as 'source has data' iff there's at least one row
    where role='waveform' AND exclude_reason IS NULL AND
    the filename date matches year. Filename date IS authoritative —
    bogus-date dirs (1900/1970/etc.) have file_year matching the real
    filename date, which is correct.
    """
    import sqlite3
    db = station_dbs / f"{network}.{sta}.db"
    if not db.exists():
        return set()
    out: set[date] = set()
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "SELECT DISTINCT file_year, file_month, file_day "
            "FROM files "
            "WHERE role='waveform' AND exclude_reason IS NULL "
            "  AND file_year=? "
            "  AND file_month BETWEEN 1 AND 12 "
            "  AND file_day BETWEEN 1 AND 31",
            (year,))
        for y, m, d in cur:
            try:
                out.add(date(y, m, d))
            except (ValueError, TypeError):
                continue
    finally:
        con.close()
    return out


def list_lt_days(lt_root: Path, net: str, sta: str, year: int) -> set[date]:
    """Union of dates that have at least one LT day-file across channels."""
    base = lt_root / str(year) / net / sta
    if not base.is_dir():
        return set()
    out: set[date] = set()
    for cha_dir in os.listdir(base):
        cha_path = base / cha_dir
        if not cha_path.is_dir():
            continue
        for f in os.listdir(cha_path):
            # SDS day-file pattern: <NET>.<STA>.<LOC>.<CHA>.D.<YEAR>.<DOY>
            m = re.fullmatch(r".+\.D\.\d{4}\.(\d{3})", f)
            if not m:
                continue
            doy = int(m.group(1))
            try:
                dt = date(year, 1, 1) + timedelta(days=doy - 1)
                if dt.year != year:
                    continue  # safety: doy can't span years
            except (ValueError, OverflowError):
                continue
            out.add(dt)
    return out


def load_plan(plans_dir: Path, network: str, sta: str) -> dict | None:
    p = plans_dir / f"{network}.{sta}.plan.yaml"
    if not p.exists():
        return None
    try:
        return yaml.safe_load(p.open())
    except Exception:
        return None


def plan_flagged_for_year(plan: dict | None, year: int) -> set[date]:
    """Dates from plan.flagged_days that fall in the given year."""
    if not plan:
        return set()
    out: set[date] = set()
    for entry in plan.get("flagged_days", []) or []:
        ds = entry.get("date") if isinstance(entry, dict) else None
        if not ds:
            continue
        try:
            dt = date.fromisoformat(ds)
        except ValueError:
            continue
        if dt.year == year:
            out.add(dt)
    return out


def load_latest_manifest(queue_dir: Path, network: str, sta: str,
                         year: int) -> dict | None:
    """Return the latest run_manifest for this (sta, year), preferring
    runs that include explicit recovery markers (most complete date
    range), then by timestamp."""
    mdir = queue_dir / "run_manifests"
    if not mdir.is_dir():
        return None
    cands = list(mdir.glob(f"eqserver_{network}_{sta}_{year}_*.json"))
    if not cands:
        return None
    cands.sort()
    # Walk back from the latest and prefer one with the most per_date_status
    # entries (closest proxy for "widest date range attempted").
    best = None
    best_count = -1
    for p in cands:
        try:
            d = json.load(p.open())
        except Exception:
            continue
        eq = d.get("eqserver") or {}
        pds = eq.get("per_date_status") or []
        if len(pds) > best_count:
            best = d
            best_count = len(pds)
    return best


def manifest_empty_dates(manifest: dict | None) -> set[date]:
    """Dates the manifest reports as no_files (legitimately empty)."""
    out: set[date] = set()
    if not manifest:
        return out
    eq = manifest.get("eqserver") or {}
    for e in eq.get("per_date_status") or []:
        if e.get("status") == "no_files":
            try:
                out.add(date.fromisoformat(e["date"]))
            except (KeyError, ValueError):
                pass
    return out


def manifest_status_index(manifest: dict | None) -> dict[date, dict]:
    """Map date -> per_date_status entry for sub-classification."""
    out: dict[date, dict] = {}
    if not manifest:
        return out
    eq = manifest.get("eqserver") or {}
    for e in eq.get("per_date_status") or []:
        try:
            out[date.fromisoformat(e["date"])] = e
        except (KeyError, ValueError):
            pass
    return out


def categorize_gap_day(dt: date, manifest_idx: dict[date, dict]) -> str:
    """Sub-classify an unintentional gap day."""
    entry = manifest_idx.get(dt)
    if entry is None:
        return "never_attempted"
    s = entry.get("status", "?")
    if s in ("timeout", "error", "parse_error", "qc_flagged"):
        return f"failed_status_{s}"
    if s == "ok" and entry.get("bytes_written", 0) == 0:
        return "ok_but_zero_bytes"
    return f"other_{s}"


def audit_unit(sta: str, year: int, network: str,
               station_dbs: Path, lt_root: Path, plans_dir: Path,
               queue_dir: Path) -> dict | None:
    src = list_source_days(station_dbs, network, sta, year)
    if not src:
        return None  # no source data — not a gap, just not in scope
    lt = list_lt_days(lt_root, network, sta, year)
    plan = load_plan(plans_dir, network, sta)
    plan_flagged = plan_flagged_for_year(plan, year)
    manifest = load_latest_manifest(queue_dir, network, sta, year)
    empty = manifest_empty_dates(manifest)
    manifest_idx = manifest_status_index(manifest)

    intentional = (plan_flagged | empty) & src
    unintentional = src - lt - plan_flagged - empty

    sub = defaultdict(list)
    for dt in sorted(unintentional):
        sub[categorize_gap_day(dt, manifest_idx)].append(dt.isoformat())

    return {
        "sta": sta,
        "year": year,
        "n_source": len(src),
        "n_lt": len(lt),
        "n_plan_flagged": len(plan_flagged),
        "n_manifest_empty": len(empty),
        "n_intentional_gap": len(intentional),
        "n_unintentional_gap": len(unintentional),
        "subcategories": dict(sub),
        "manifest_present": manifest is not None,
        "manifest_per_date_status_count": len(manifest_idx),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", required=True)
    ap.add_argument("--station-dbs", required=True,
                    help="dir containing per-station Level-1 DBs (~/station_dbs)")
    ap.add_argument("--lt-root", required=True,
                    help="LT root (CIFS ro from staging VM)")
    ap.add_argument("--queue-dir", required=True,
                    help="queue dir with run_manifests/")
    ap.add_argument("--plans", required=True,
                    help="plans/<network>/ dir")
    ap.add_argument("--network", default="VW")
    ap.add_argument("--stations", default="",
                    help="comma-separated subset; default all include:true")
    ap.add_argument("--year-min", type=int, default=2012)
    ap.add_argument("--year-max", type=int, default=2025)
    ap.add_argument("--out", default="-",
                    help="output file (- for stdout)")
    args = ap.parse_args()

    station_dbs = Path(args.station_dbs)
    lt_root = Path(args.lt_root)
    plans_dir = Path(args.plans)
    queue_dir = Path(args.queue_dir)

    reg = yaml.safe_load(open(args.registry))

    target = []
    wanted_subset = {s.strip() for s in args.stations.split(",") if s.strip()}
    for sta, e in reg.items():
        if not isinstance(e, dict):
            continue
        if e.get("include") is not True:
            continue
        if e.get("target_network") != args.network:
            continue
        if wanted_subset and sta not in wanted_subset:
            continue
        target.append(sta)
    target.sort()

    print(f"[audit] network={args.network}  stations={len(target)}  "
          f"years={args.year_min}-{args.year_max}", file=sys.stderr)

    fh = sys.stdout if args.out == "-" else open(args.out, "w")

    fh.write(f"# Source vs LT gap audit — network={args.network}\n")
    fh.write(f"# {len(target)} stations, years {args.year_min}-{args.year_max}\n")
    fh.write("# Columns: sta year n_src n_lt n_flag n_empty n_intent n_UNINT "
             "sub_breakdown\n")
    fh.write("#" + "-" * 100 + "\n")

    summary = []
    grand_unint = 0
    grand_src = 0
    for sta in target:
        for yr in range(args.year_min, args.year_max + 1):
            row = audit_unit(sta, yr, args.network, station_dbs, lt_root,
                             plans_dir, queue_dir)
            if row is None:
                continue
            grand_unint += row["n_unintentional_gap"]
            grand_src += row["n_source"]
            sub = ";".join(f"{k}={len(v)}" for k, v in
                           sorted(row["subcategories"].items()))
            warn = "!!" if row["n_unintentional_gap"] > 0 else "  "
            fh.write(f"{warn} {sta:6s} {yr}  src={row['n_source']:3d} "
                     f"lt={row['n_lt']:3d}  flag={row['n_plan_flagged']:3d} "
                     f"empty={row['n_manifest_empty']:3d}  "
                     f"intent={row['n_intentional_gap']:3d}  "
                     f"UNINT={row['n_unintentional_gap']:3d}  "
                     f"[{sub}]\n")
            if row["n_unintentional_gap"] > 0:
                summary.append(row)
                # Also dump the gap dates for the worst offenders
                if row["n_unintentional_gap"] >= 10:
                    for cat, dates in row["subcategories"].items():
                        fh.write(f"     gap-cat {cat}: {len(dates)} days, "
                                 f"first={dates[0] if dates else '-'}\n")

    fh.write("\n")
    fh.write(f"GRAND TOTAL UNINTENTIONAL GAP DAYS: {grand_unint}\n")
    fh.write(f"GRAND TOTAL SOURCE DAYS:            {grand_src}\n")
    fh.write(f"Gap fraction: "
             f"{grand_unint/grand_src*100 if grand_src else 0:.1f}%\n")
    fh.write(f"Units with non-zero unintentional gap: {len(summary)}\n")
    fh.write("\n")
    fh.write("Top 15 unit-gaps by absolute count:\n")
    summary.sort(key=lambda r: -r["n_unintentional_gap"])
    for r in summary[:15]:
        fh.write(f"  {r['sta']:6s} {r['year']}  "
                 f"unint={r['n_unintentional_gap']:3d} / src={r['n_source']:3d}  "
                 f"({r['n_unintentional_gap']/r['n_source']*100:.1f}%)\n")
    if args.out != "-":
        fh.close()


if __name__ == "__main__":
    sys.exit(main())

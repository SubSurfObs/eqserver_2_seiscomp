#!/usr/bin/env python3
"""sweep_status.py — summarise the state of a production sweep.

TWO MODES:

1. `--unit-table` (canonical): join all four queue jsonl files
   (convert_done, convert_failed, promote_done, held, cleanup_done) by
   (net, sta, year) and print a per-unit state table that folds in
   action="resolved" events from convert_failed.jsonl. **This is the gate
   any retry-class decision MUST consult.** convert_failed.jsonl alone
   is misleading because it's append-only — a unit can be "failed" there
   AND already recovered via run_recovery_register.py. Only this view
   sees both.

   Usage:
     python3 scan/sweep_status.py \\
         --queue-dir /mnt/seiscomp_staging/eqserver_sweep \\
         --unit-table
     # or filtered to specific units:
     python3 scan/sweep_status.py \\
         --queue-dir ... --unit-table \\
         --unit VW.DDNE.2017,VW.DDSW.2019

2. `--convert-log <path>` (legacy summary): mid-sweep operator dashboard
   — fail density, recent failures, retry list. Useful during a long
   sweep when you want to see "are we still healthy?" without a per-unit
   table.

Output sections in legacy mode:
  - Headline counts: convert ok / fail / in-flight, promote committed /
    held / skip-empty.
  - Fail density: most-recent-N units' fail rate. Surfaces trends.
  - Recent fails: last 10 with last-successful-phase3-day.
  - Retry list: stations + years not in convert_done.

ARCHITECTURE NOTE: convert_failed.jsonl is APPEND-ONLY and never expires
entries even after recovery. The action="resolved" event added by
run_recovery_register.py on successful recovery is what makes it
reconcilable. Anything that asks "what's outstanding?" must consult the
--unit-table view, not convert_failed.jsonl directly.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


CONVERT_LINE_RE = re.compile(
    r"\[convert\]\s+\[(\d+)/(\d+)\]\s+(OK|FAIL)\s+(\S+)\s+(\d+)"
)


def parse_convert_log(path: Path) -> list[dict]:
    """Return list of {idx, total, status, sta, year} from convert.py main log."""
    results = []
    if not path.exists():
        return results
    for line in path.open():
        m = CONVERT_LINE_RE.search(line)
        if m:
            results.append({
                "idx": int(m.group(1)),
                "total": int(m.group(2)),
                "status": m.group(3),
                "sta": m.group(4),
                "year": int(m.group(5)),
            })
    return results


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.open():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def build_unit_table(queue: Path,
                     filter_units: list[tuple[str, str, int]] | None = None
                    ) -> list[dict]:
    """Join all four queue jsonl files by (net, sta, year) and return one row
    per unit with a `state` field that folds in resolved-failure events.

    State precedence (highest to lowest, terminal first):
      cleaned   — any cleanup_done.jsonl entry (excluding upstream-skip
                  bookkeeping)
      promoted  — any promote_done.jsonl entry with action=="promoted"
                  (write/override/skip irrelevant; presence in promote_done
                  IS the signal)
      held      — any held.jsonl entry not subsequently cleared
      failed-resolved
                — convert_failed.jsonl has both an action=="failed" event
                  AND a matching action=="resolved" event (linked by
                  original_run_id), AND a convert_done entry exists. The
                  resolved-event link is the canonical signal; do NOT retry.
      failed-unresolved
                — convert_failed.jsonl has an action=="failed" or no-action
                  (legacy) event with NO matching resolved event. THIS is
                  the only state that means "retry me."
      converted-not-yet-promoted
                — in convert_done.jsonl, not (yet) in any later state.
      not-attempted
                — none of the above.

    Multi-run wrinkle: a unit can have many run_ids across many events. We
    treat each state-class as "membership" (at least one event), and the
    precedence above resolves the current state. The state column tells
    you what to do next, not the full audit history.
    """
    convert_done = read_jsonl(queue / "convert_done.jsonl")
    convert_failed = read_jsonl(queue / "convert_failed.jsonl")
    promote_done = read_jsonl(queue / "promote_done.jsonl")
    held = read_jsonl(queue / "held.jsonl")
    cleanup_done = read_jsonl(queue / "cleanup_done.jsonl")

    def key(e):
        return (e.get("net", "?"), e.get("sta", "?"), e.get("year"))

    # Build per-unit event sets
    units: dict[tuple, dict] = {}

    def get(k):
        if k not in units:
            units[k] = {
                "net": k[0], "sta": k[1], "year": k[2],
                "converted_run_ids": [],
                "failed_events": [],     # action != resolved
                "resolved_for_run_ids": set(),  # original_run_ids resolved
                "promoted": False,
                "held": False,
                "cleaned": False,
                "last_promote_summary": None,
                "last_recovery_run_id": None,
            }
        return units[k]

    for e in convert_done:
        u = get(key(e))
        u["converted_run_ids"].append(e.get("run_id"))
        if e.get("recovery"):
            u["last_recovery_run_id"] = e.get("run_id")

    for e in convert_failed:
        u = get(key(e))
        if e.get("action") == "resolved":
            orig = e.get("original_run_id")
            if orig:
                u["resolved_for_run_ids"].add(orig)
            else:
                # Backfilled / unknown-original resolved: treat ALL unit
                # failed events as resolved (operator marked the unit
                # recovered without identifying a specific original).
                u["resolved_for_run_ids"].add("__all__")
        else:
            u["failed_events"].append(e)

    for e in promote_done:
        if e.get("action") != "promoted":
            continue
        u = get(key(e))
        u["promoted"] = True
        u["last_promote_summary"] = e.get("summary")

    for e in held:
        u = get(key(e))
        u["held"] = True

    for e in cleanup_done:
        if e.get("action") != "cleaned":
            continue
        u = get(key(e))
        u["cleaned"] = True

    # Compute state
    rows = []
    for k, u in units.items():
        unresolved = []
        for ev in u["failed_events"]:
            rid = ev.get("run_id")
            if "__all__" in u["resolved_for_run_ids"]:
                continue
            if rid in u["resolved_for_run_ids"]:
                continue
            unresolved.append(ev)
        if u["cleaned"]:
            state = "cleaned"
        elif u["promoted"]:
            state = "promoted"
        elif u["held"]:
            state = "held"
        elif unresolved:
            state = "failed-unresolved"
        elif u["failed_events"]:
            state = "failed-resolved"
        elif u["converted_run_ids"]:
            state = "converted-not-yet-promoted"
        else:
            state = "not-attempted"
        rows.append({
            "net": k[0], "sta": k[1], "year": k[2],
            "state": state,
            "n_failed_events": len(u["failed_events"]),
            "n_unresolved": len(unresolved),
            "n_converted": len(u["converted_run_ids"]),
            "promoted": u["promoted"],
            "held": u["held"],
            "cleaned": u["cleaned"],
            "last_promote_summary": u["last_promote_summary"],
            "last_recovery_run_id": u["last_recovery_run_id"],
        })
    rows.sort(key=lambda r: (r["net"], r["sta"], r["year"] or 0))

    if filter_units:
        wanted = {(n, s, y) for n, s, y in filter_units}
        rows = [r for r in rows if (r["net"], r["sta"], r["year"]) in wanted]

    return rows


def print_unit_table(rows: list[dict]) -> None:
    """Pretty-print the unit-table rows + state-distribution summary."""
    print("=" * 86)
    print("SWEEP UNIT TABLE — CURRENT STATE per (net, sta, year)")
    print("=" * 86)
    print()
    # State distribution headline
    state_counts = Counter(r["state"] for r in rows)
    state_order = [
        "not-attempted",
        "failed-unresolved",
        "converted-not-yet-promoted",
        "held",
        "failed-resolved",
        "promoted",
        "cleaned",
    ]
    print("  State distribution:")
    for s in state_order:
        if s in state_counts:
            marker = "!!" if s == "failed-unresolved" else "  "
            print(f"    {marker} {s:30s}  {state_counts[s]:5d}")
    for s, n in state_counts.items():
        if s not in state_order:
            print(f"       {s:30s}  {n:5d}")
    print()
    print(f"  Total: {len(rows)} units")
    print()
    # If there's anything unresolved, list it explicitly — this is the
    # gate-line for retry decisions.
    unresolved = [r for r in rows if r["state"] == "failed-unresolved"]
    if unresolved:
        print("  UNRESOLVED FAILURES (action required — retry candidates):")
        for r in unresolved:
            print(f"    {r['net']}.{r['sta']}.{r['year']} "
                  f"({r['n_unresolved']} unresolved event(s))")
        print()
    else:
        print("  No unresolved failures. Nothing to retry.")
        print()
    print("=" * 86)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--convert-log",
                    help="path to the convert.py main log (legacy mode only)")
    ap.add_argument("--queue-dir", required=True,
                    help="path to the queue dir (e.g. /mnt/seiscomp_staging/eqserver_sweep)")
    ap.add_argument("--recent-window", type=int, default=20,
                    help="how many most-recent units to compute fail density over")
    ap.add_argument("--unit-table", action="store_true",
                    help="print per-(net,sta,year) unit-table — the canonical "
                         "'what's currently outstanding?' view. Folds in "
                         "convert_failed.jsonl resolved events. Use this "
                         "BEFORE any retry-class decision.")
    ap.add_argument("--unit",
                    help="with --unit-table: filter to specific units "
                         "(comma-separated NET.STA.YEAR; e.g. "
                         "VW.DDNE.2017,VW.DDSW.2019)")
    args = ap.parse_args()

    queue = Path(args.queue_dir)

    if args.unit_table:
        filter_units = None
        if args.unit:
            filter_units = []
            for tok in args.unit.split(","):
                parts = tok.strip().split(".")
                if len(parts) == 3:
                    filter_units.append((parts[0], parts[1], int(parts[2])))
        rows = build_unit_table(queue, filter_units=filter_units)
        print_unit_table(rows)
        return 0

    if not args.convert_log:
        ap.error("--convert-log required for legacy summary mode "
                 "(or use --unit-table for the canonical view)")
    convert_log = Path(args.convert_log)

    log_results = parse_convert_log(convert_log)
    convert_done = read_jsonl(queue / "convert_done.jsonl")
    convert_failed = read_jsonl(queue / "convert_failed.jsonl")
    promote_done = read_jsonl(queue / "promote_done.jsonl")
    held = read_jsonl(queue / "held.jsonl")

    # Headline counts
    n_ok = sum(1 for r in log_results if r["status"] == "OK")
    n_fail = sum(1 for r in log_results if r["status"] == "FAIL")
    total = log_results[-1]["total"] if log_results else 0
    in_flight = max(0, len({(r["sta"], r["year"]) for r in log_results}) - n_ok - n_fail)

    n_promoted = sum(1 for e in promote_done if e.get("action") == "promoted")
    n_skip_empty = sum(1 for e in promote_done if e.get("action") == "skip-empty")
    n_held = len(held)

    print("=" * 70)
    print("SWEEP STATUS")
    print("=" * 70)
    print(f"  convert.py log: {convert_log}")
    print(f"  queue dir:      {queue}")
    print()
    print(f"  Convert:  {n_ok:4d} ok   {n_fail:4d} fail   {in_flight:2d} in-flight   "
          f"of {total} total units")
    print(f"  Promote:  {n_promoted:4d} promoted   {n_skip_empty:4d} skip-empty   {n_held:2d} held")
    print()

    # Fail density (overall + recent)
    n_attempted = n_ok + n_fail
    if n_attempted > 0:
        overall_rate = n_fail / n_attempted * 100
        recent = log_results[-args.recent_window:]
        recent_fails = sum(1 for r in recent if r["status"] == "FAIL")
        recent_attempted = len(recent)
        recent_rate = (recent_fails / recent_attempted * 100) if recent_attempted else 0.0
        print(f"  Fail density (overall):           {n_fail}/{n_attempted} = {overall_rate:5.1f}%")
        print(f"  Fail density (last {recent_attempted:2d} units):     "
              f"{recent_fails}/{recent_attempted} = {recent_rate:5.1f}%")
        if recent_rate >= 20:
            print(f"  !! WARNING: recent fail rate ≥ 20% — consider aborting + investigating.")
        elif recent_rate >= 10:
            print(f"  !! CAUTION: recent fail rate ≥ 10% — watch closely.")
        print()

    # Recent fails detail
    if convert_failed:
        print(f"  Recent failures (last 10):")
        for f in convert_failed[-10:]:
            sta = f.get("sta")
            year = f.get("year")
            rc = f.get("rc")
            elapsed = f.get("elapsed_s", 0)
            last_day = f.get("last_successful_phase3_day", "") or ""
            # Trim the day-line to first 60 chars
            last_day = last_day[:60] + ("..." if len(last_day) > 60 else "")
            print(f"    {sta} {year}: rc={rc} elapsed={elapsed}s  last_ok={last_day}")
        print()

    # Failure clustering by station / by recorder-type-band (gecko year vs echopro year)
    if convert_failed:
        by_sta = Counter(f["sta"] for f in convert_failed)
        print("  Failures by station:")
        for sta, n in by_sta.most_common(10):
            years = [f["year"] for f in convert_failed if f["sta"] == sta]
            print(f"    {sta}: {n} year(s)  {years}")
        print()

    # In-flight: look for the most recent unit start line that has no matching OK/FAIL
    in_flight_re = re.compile(r"\[convert\]\s+\[(\d+)/(\d+)\]\s+(\S+)\s+(\d+)\s+\.\.\.\s*$")
    last_start = None
    for line in convert_log.open():
        m = in_flight_re.search(line)
        if m:
            last_start = (m.group(3), int(m.group(4)))
    if last_start:
        done_keys = {(r["sta"], r["year"]) for r in log_results}
        if last_start not in done_keys:
            print(f"  In flight: {last_start[0]} {last_start[1]}")
            print()

    # Retry list — union of convert_failed.jsonl + log-derived FAIL lines
    failed_keys = {(f["sta"], f["year"]) for f in convert_failed}
    for r in log_results:
        if r["status"] == "FAIL":
            failed_keys.add((r["sta"], r["year"]))
    if failed_keys:
        # Prerequisites checklist — surface before the retry CLI so the
        # operator can't accidentally launch a fresh phase3 without
        # closing the open provenance/engine items first.
        print("  PREREQUISITES before retry pass (these MUST be done first):")
        print("    [ ] VM disk_to_sds checkout reconciled to >= 9a3b2ae")
        print("        ssh dsand@172.26.144.41 'cd ~/projects/SubSurfObs/disk_to_sds && \\")
        print("            sha256sum scripts/suds_convert.py && \\")
        print("            rm scripts/suds_convert.py && \\")
        print("            git pull --ff-only origin main && \\")
        print("            sha256sum scripts/suds_convert.py'")
        print("        # Expect post-pull sha256 == 7f625589...")
        print("    [ ] engine_git source-dict schema bumped in apply.py")
        print("        See agent memory project-engine-provenance-incident-2026-06-01")
        print()

    # Day-level retry candidates (separate from unit-level retry) — these
    # are days where ANY unit recorded parse_error in its run_manifest.
    # Mid-sweep these can't be recovered (the unit already promoted with
    # zero traces for that day), so they need a per-day retry pass after
    # the main sweep + BEST 2019 unit-retry are both done.
    if failed_keys:
        print("  DAY-LEVEL RETRY PASS (after sweep + unit-retries complete):")
        print("    A separate pass should walk all run_manifests for status='parse_error'")
        print("    days and re-convert them (gecko bulk-fallback now landed in a6894fb")
        print("    means this WILL recover them). Known affected so far: 13 BRTH days")
        print("    across 2020-2024. Surfaced via:")
        print("      ls /mnt/seiscomp_staging/eqserver_sweep/run_manifests/*.json | \\")
        print("        xargs -I{} jq -r '.eqserver.per_date_status[] | \\")
        print("                          select(.status==\"parse_error\") | .date' {}")
        print()
        print("  Retry list (re-run after main sweep completes):")
        # Group by station for compact display
        by_sta_retry = defaultdict(list)
        for sta, year in failed_keys:
            by_sta_retry[sta].append(year)
        for sta in sorted(by_sta_retry):
            years = sorted(by_sta_retry[sta])
            year_summary = ",".join(str(y) for y in years)
            print(f"    {sta}: {year_summary}")
        print()
        # Compact CLI to re-run all in one go
        stations = sorted(set(s for s, _ in failed_keys))
        ymin = min(y for _, y in failed_keys)
        ymax = max(y for _, y in failed_keys)
        print("  To retry ALL failed units after the main sweep:")
        print(f"    python3 scan/run_production_convert.py --network VW \\")
        print(f"        --stations {','.join(stations)} \\")
        print(f"        --year-min {ymin} --year-max {ymax} \\")
        print(f"        --registry metadata/station_registry.yaml \\")
        print(f"        --staging-sds /mnt/seiscomp_staging/seiscomp_archive \\")
        print(f"        --queue-dir /mnt/seiscomp_staging/eqserver_sweep \\")
        print(f"        --workers 4 --log-dir /var/tmp/eqserver_retry_logs")
        print()

    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())

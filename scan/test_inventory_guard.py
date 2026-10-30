#!/usr/bin/env python3
"""test_inventory_guard — Codex launch review F1 regressions (stdlib only).

1. An old staged day-file survives no committed day outcome: the shared
   reconciler quarantines it (moved, not deleted) when the current run
   writes nothing for that day.
2. The reconciler keeps exactly the files the current run wrote.
3. The promote guard flags any staged file the run manifest does not
   name, passes an exact inventory, and fails closed on an unreadable
   manifest.
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase3_driver import _quarantine_stale_day  # noqa: E402
from run_production_promote import unnamed_staged_files  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)
        if detail:
            print(f"      {detail}")


def seed(root, chan, jday, year=2023, net="DU", sta="FAKE", body=b"old"):
    p = (root / str(year) / net / sta / f"{chan}.D"
         / f"{net}.{sta}.00.{chan}.D.{year}.{jday:03d}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return p


def main():
    day = date(2023, 7, 28)            # julian day 209
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # 1. Empty current outcome: the old file must leave the active tree.
        old = seed(root, "HHZ", 209)
        other_day = seed(root, "HHZ", 210)
        moved = _quarantine_stale_day(root, "DU", "FAKE", day)
        check("old file quarantined on empty day", not old.exists()
              and len(moved) == 1, f"moved={moved}")
        q = list((root / ".stale_quarantine").rglob(old.name))
        check("quarantined, not deleted", len(q) == 1)
        check("other days untouched", other_day.exists())

        # 2. Written outcome: the run's own file is kept, a stale sibling
        #    channel from an earlier run is quarantined.
        keep = seed(root, "HHZ", 209, body=b"new")
        stale_chan = seed(root, "HHE", 209)
        _quarantine_stale_day(root, "DU", "FAKE", day, keep={keep})
        check("run's own file kept", keep.exists()
              and keep.read_bytes() == b"new")
        check("stale sibling channel quarantined", not stale_chan.exists())

        # 3. Promote guard against a run manifest.
        mpath = root / "manifest.json"
        entry = {"run_manifest_path": str(mpath), "staging_root": str(root),
                 "net": "DU", "sta": "FAKE", "year": 2023}
        named = [[str(keep), keep.stat().st_size],
                 [str(other_day), other_day.stat().st_size]]
        mpath.write_text(json.dumps({"eqserver": {"per_date_status": [
            {"date": "2023-07-28", "sds_files_written": named}]}}))
        check("exact inventory passes",
              unnamed_staged_files(entry) == [],
              str(unnamed_staged_files(entry)))

        rogue = seed(root, "HHN", 211)
        got = unnamed_staged_files(entry)
        check("unnamed staged file is flagged", got == [str(rogue)], str(got))

        mpath.write_text("{not json")
        check("unreadable manifest fails closed",
              unnamed_staged_files(entry) is None)

    # 4. Completion rule (Codex 24a8243): any failed day => not complete.
    from run_production_convert import manifest_day_failures
    with tempfile.TemporaryDirectory() as td2:
        m = Path(td2) / "m.json"
        m.write_text(json.dumps({"aggregate": {"items_succeeded": 2},
            "eqserver": {"per_date_status": [
                {"date": "2025-10-11", "status": "ok"},
                {"date": "2025-10-12", "status": "error"},
                {"date": "2025-10-13", "status": "no_files"}]}}))
        ok, bad, readable = manifest_day_failures(m)
        check("failed day detected, unit not complete",
              readable and bad == ["2025-10-12"], f"{bad}")
        m.write_text(json.dumps({"aggregate": {"items_succeeded": 2},
            "eqserver": {"per_date_status": [
                {"date": "2025-10-11", "status": "ok"},
                {"date": "2025-10-12", "status": "no_files"}]}}))
        check("clean unit (ok + no_files) is complete",
              manifest_day_failures(m)[1] == [])
        for st in ("blocked_identity", "qc_flagged", "something_new"):
            m.write_text(json.dumps({"eqserver": {"per_date_status": [
                {"date": "2025-10-11", "status": "ok"},
                {"date": "2025-10-12", "status": st}]}}))
            check(f"allowlist: '{st}' day => not complete",
                  manifest_day_failures(m)[1] == ["2025-10-12"])
        # Codex 7bd2d31 (1): partial_completion or a short/garbled day list.
        full = [{"date": f"2025-01-0{i}", "status": "ok"} for i in (1, 2, 3)]
        m.write_text(json.dumps({"eqserver": {"per_date_status": full}}))
        check("full window passes",
              manifest_day_failures(m, "2025-01-01", "2025-01-03")[1] == [])
        m.write_text(json.dumps({"eqserver": {"per_date_status": full,
            "partial_completion": True,
            "partial_completion_reason": "pool heartbeat"}}))
        check("partial_completion => not complete",
              manifest_day_failures(m, "2025-01-01", "2025-01-03")[1] != [])
        m.write_text(json.dumps({"eqserver": {"per_date_status": full[:2]}}))
        check("truncated day list => not complete",
              manifest_day_failures(m, "2025-01-01", "2025-01-03")[1] != [])
        m.write_text(json.dumps({"eqserver": {"per_date_status":
            full[:2] + [full[1]]}}))
        check("duplicate day replacing a missing one => not complete",
              manifest_day_failures(m, "2025-01-01", "2025-01-03")[1] != [])
        m.write_text("{broken")
        check("unreadable manifest is never complete",
              manifest_day_failures(m)[2] is False)

    # Codex 7bd2d31 (2): zero-output day downgrade never hides a read error.
    from phase3_driver import _zero_output_day_status
    day0 = 1_000_000_000_000
    prior = [(None, None, None, None, day0 - 60_000_000_000)]
    today = [(None, None, None, None, day0 + 60_000_000_000)]
    check("prior-day spill only, no read error => no_files",
          _zero_output_day_status(prior, day0, []) == "no_files")
    check("prior-day spill WITH read error => error",
          _zero_output_day_status(prior, day0, [{"err": "x"}]) == "error")
    check("own decisions, zero output => error",
          _zero_output_day_status(today, day0, []) == "error")

    if FAILURES:
        print(f"\n{len(FAILURES)} inventory-guard test(s) FAILED: {FAILURES}")
        return 1
    print("\nall inventory-guard tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

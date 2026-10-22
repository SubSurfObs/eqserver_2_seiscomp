#!/usr/bin/env python3
"""test_selection_tails — tail_for_day returns (path, chans) per channel
group, taking the FINAL selected file per group even when it is a bundle
at an earlier hhmm (review 2026-10-23, F3). Stdlib only.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.selection import Selection  # noqa: E402
from lib import selection as sel_mod  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        manifest = td / "VW.FAKE.db"
        mc = sqlite3.connect(manifest)
        mc.execute("CREATE TABLE files (path TEXT)")
        mc.execute("INSERT INTO files VALUES ('x')")
        mc.commit()
        mc.close()
        ident = sel_mod._manifest_identity(manifest)

        sp = td / "sel.sqlite"
        c = sqlite3.connect(sp)
        c.executescript(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE selection (date TEXT, stream TEXT, population TEXT,"
            " path TEXT, hhmm TEXT, span_s REAL, role TEXT, rule TEXT,"
            " chans TEXT, PRIMARY KEY (date, stream, path, chans));")
        c.executemany("INSERT INTO meta VALUES (?,?)", [
            ("schema_version", "selection-v0.3"), ("complete_marker", "1"),
            ("net", "VW"), ("sta", "FAKE"), ("year", "2023"),
            ("manifest_identity", ident), ("probe_enabled", "True"),
            ("probe_blocked", "[]")])
        rows = [
            # Z comes from disk minute-files; last one is 2359.
            ("2023-05-01", "s", "disk", "/d/0000_z.dmx", "0000", 60, "w", "r", "Z"),
            ("2023-05-01", "s", "disk", "/d/2359_z.dmx", "2359", 60, "w", "r", "Z"),
            # N+E come from a 23-hour bundle whose hhmm is 0100 — it IS the
            # final NE coverage of the day (no later NE file exists).
            ("2023-05-01", "s", "tele", "/t/bundle_ne.ms", "0100", 82800, "w", "r", "NE"),
            ("2023-05-01", "s", "tele", "/t/0030_ne.ms", "0030", 60, "w", "r", "NE"),
            # A fill row: must sort AFTER every winner row (F5 priority).
            ("2023-05-01", "s", "tele", "/t/0000_z_fill.ms", "0000", 60, "w",
             "short_fill:Z:tele", "Z"),
        ]
        c.executemany("INSERT INTO selection VALUES (?,?,?,?,?,?,?,?,?)", rows)
        c.commit()
        c.close()

        s = Selection(sp, "VW", "FAKE", 2023, manifest)
        tails = s.tail_for_day("2023-05-01")
        day_rows = s.files_for_day("2023-05-01")
        s.close()

        ok = (day_rows[-1][0] == "/t/0000_z_fill.ms"
              and all(not r[2].startswith(("short_fill", "gap_fill"))
                      for r in day_rows[:-1]))
        print(f"{'PASS' if ok else 'FAIL'}  fill rows sort after winners")
        if not ok:
            print(f"      got {day_rows!r}")
            return 1

        want = [("/d/2359_z.dmx", "Z"), ("/t/bundle_ne.ms", "NE")]
        ok = sorted(tails) == sorted(want)
        print(f"{'PASS' if ok else 'FAIL'}  tail_for_day per-group with bundle")
        if not ok:
            print(f"      got {tails!r}, want {want!r}")
            return 1

        # F4 loader refusals: unprobed and probe-blocked selections.
        c = sqlite3.connect(sp)
        c.execute("UPDATE meta SET value='False' WHERE key='probe_enabled'")
        c.commit(); c.close()
        try:
            Selection(sp, "VW", "FAKE", 2023, manifest)
            print("FAIL  no-probe selection refused"); return 1
        except Exception as e:
            ok = "no-probe" in str(e)
            print(f"{'PASS' if ok else 'FAIL'}  no-probe selection refused")
            if not ok:
                return 1
        c = sqlite3.connect(sp)
        c.execute("UPDATE meta SET value='True' WHERE key='probe_enabled'")
        c.execute("UPDATE meta SET value=? WHERE key='probe_blocked'",
                  ('[{"population": "disk|x", "basis": "probe_failed", '
                   '"days": 3}]',))
        c.commit(); c.close()
        try:
            Selection(sp, "VW", "FAKE", 2023, manifest)
            print("FAIL  probe-blocked selection refused"); return 1
        except Exception as e:
            ok = "probe-blocked" in str(e)
            print(f"{'PASS' if ok else 'FAIL'}  probe-blocked selection refused")
            if not ok:
                return 1

    print("all selection-tail tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

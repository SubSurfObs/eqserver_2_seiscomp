#!/usr/bin/env python3
"""test_selection_tails — the v0.4 loader contract: decisions_for_day
window intersection (including the previous day's boundary_tail spill),
per-day blocking, and the fail-closed refusals. Stdlib only."""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.selection import Selection  # noqa: E402
from lib import selection as sel_mod  # noqa: E402

NS = 1_000_000_000
D0 = 1682899200  # 2023-05-01 00:00 UTC
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILURES.append(name)
        if detail:
            print(f"      {detail}")


def build(td):
    manifest = td / "VW.FAKE.db"
    mc = sqlite3.connect(manifest)
    mc.execute("CREATE TABLE files (path TEXT, station TEXT, "
               "file_year INT, file_month INT, file_day INT)")
    for p, dd in (("/d/0000_z.dmx", 1), ("/t/bundle_ne.ms", 1),
                  ("/d/2359_z.dmx", 1), ("/d/j2.dmx", 2)):
        mc.execute("INSERT INTO files VALUES (?,?,?,?,?)",
                   (p, "FAKE", 2023, 5, dd))
    mc.commit()
    mc.close()
    sp = td / "sel.sqlite"
    c = sqlite3.connect(sp)
    c.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE selection (date TEXT, stream TEXT, population TEXT,"
        " path TEXT, hhmm TEXT, span_s REAL, role TEXT, rule TEXT,"
        " chans TEXT, PRIMARY KEY (date, stream, path, chans));"
        "CREATE TABLE selected_file (file_id INTEGER PRIMARY KEY,"
        " date TEXT, stream TEXT, path TEXT, population TEXT,"
        " source_format TEXT, UNIQUE(date, stream, path));"
        "CREATE TABLE component_decision (decision_id TEXT, file_id INT,"
        " orientation TEXT, window_start_ns INT, window_end_ns INT,"
        " priority INT, selection_reason TEXT,"
        " PRIMARY KEY(decision_id, file_id, orientation,"
        " window_start_ns, window_end_ns));"
        "CREATE TABLE blocked_days (date TEXT, population TEXT,"
        " basis TEXT, PRIMARY KEY (date, population));")
    files = [
        (1, "2023-05-01", "s", "/d/0000_z.dmx", "disk:u", "suds"),
        (2, "2023-05-01", "s", "/t/bundle_ne.ms", "tele:s", "mseed"),
        (3, "2023-05-01", "s", "/d/2359_z.dmx", "disk:u", "suds"),
        (4, "2023-05-02", "s", "/d/j2.dmx", "disk:u", "suds"),
    ]
    c.executemany("INSERT INTO selected_file VALUES (?,?,?,?,?,?)", files)
    day = D0 * NS
    nxt = day + 86400 * NS
    dec = [
        ("2023-05-01:Z", 1, "Z", day, day + 60 * NS, 0, "winner:Z:disk:u"),
        ("2023-05-01:N", 2, "N", day, nxt, 0, "winner:N:tele:s"),
        ("2023-05-01:Z", 3, "Z", nxt - 60 * NS, nxt, 0, "winner:Z:disk:u"),
        # gap-fill with a NARROW granted window (one minute at 00:02)
        ("2023-05-01:Z", 2, "Z", day + 120 * NS, day + 180 * NS, 2,
         "gap_fill:Z:tele:s"),
        # previous day's spill: authorizes the 2359 file into day 2
        ("2023-05-02:Z", 3, "Z", nxt, nxt + 60 * NS, 0, "boundary_tail"),
        ("2023-05-02:Z", 4, "Z", nxt, nxt + 86400 * NS, 0,
         "winner:Z:disk:u"),
    ]
    c.executemany("INSERT INTO component_decision VALUES (?,?,?,?,?,?,?)",
                  dec)
    c.executemany("INSERT INTO selection VALUES (?,?,?,?,?,?,?,?,?)", [
        ("2023-05-01", "s", "disk:u", "/d/0000_z.dmx", "0000", 60, "w",
         "winner:Z:disk:u", "Z")])
    n_rows = c.execute("SELECT COUNT(*) FROM selection").fetchone()[0]
    n_days = c.execute(
        "SELECT COUNT(DISTINCT date) FROM selection").fetchone()[0]
    c.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema_version", "selection-v0.4"), ("complete_marker", "1"),
        ("net", "VW"), ("sta", "FAKE"), ("year", "2023"),
        ("manifest_identity", sel_mod._manifest_identity(manifest)),
        ("manifest_sha256", sel_mod._file_sha256(manifest)),
        ("generator_sha", "deadbeef"), ("generator_dirty", "False"),
        ("probe_enabled", "True"), ("probe_blocked", "[]"),
        ("n_days", str(n_days)), ("n_rows", str(n_rows)),
        ("n_decisions", str(len(dec)))])
    c.commit()
    c.close()
    return manifest, sp


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        manifest, sp = build(td)
        s = Selection(sp, "VW", "FAKE", 2023, manifest)

        d1 = s.decisions_for_day("2023-05-01")
        check("day-1 decision count", len(d1) == 4, f"got {len(d1)}")
        gf = [r for r in d1 if r[7].startswith("gap_fill")]
        check("gap-fill window is the granted minute only",
              len(gf) == 1 and gf[0][5] - gf[0][4] == 60 * NS,
              f"got {gf}")

        d2 = s.decisions_for_day("2023-05-02")
        spill = [r for r in d2 if r[7] == "boundary_tail"]
        check("spill decision authorizes prev file into next day",
              len(spill) == 1 and spill[0][0] == "/d/2359_z.dmx"
              and spill[0][6] == 0, f"got {spill}")
        check("day-2 has its own winner too",
              any(r[0] == "/d/j2.dmx" for r in d2))

        check("blocked_for_day empty when table empty",
              s.blocked_for_day("2023-05-01") == [])
        s.close()

        # fail-closed refusals: missing n_decisions; wrong count; no-probe
        c = sqlite3.connect(sp)
        c.execute("UPDATE meta SET value='99' WHERE key='n_decisions'")
        c.commit(); c.close()
        try:
            Selection(sp, "VW", "FAKE", 2023, manifest)
            check("tampered decision count refused", False)
        except Exception as e:
            check("tampered decision count refused", "n_decisions" in str(e))
        c = sqlite3.connect(sp)
        c.execute("UPDATE meta SET value='6' WHERE key='n_decisions'")
        c.execute("UPDATE meta SET value='False' WHERE key='probe_enabled'")
        c.commit(); c.close()
        try:
            Selection(sp, "VW", "FAKE", 2023, manifest)
            check("no-probe selection refused", False)
        except Exception as e:
            check("no-probe selection refused", "no-probe" in str(e))

    if FAILURES:
        print(f"\n{len(FAILURES)} selection test(s) FAILED: {FAILURES}")
        return 1
    print("\nall selection tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

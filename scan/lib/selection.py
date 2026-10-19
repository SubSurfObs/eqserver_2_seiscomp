#!/usr/bin/env python3
"""selection — fail-closed loader for coverage_planner selection databases.

The v3 contract (docs/rework_plan.md): a selection sqlite is the converter's
ONLY source of per-day file lists. This module validates one before any
conversion job is dispatched and serves per-day file lists to workers.

Validation (any failure raises SelectionError — the converter must refuse
to run, never fall back silently):
  - schema_version is one this code knows;
  - complete_marker present (the compile finished; no truncation);
  - net/sta/year match what the converter was asked to convert;
  - manifest_identity matches the manifest DB the converter was given
    (same size/mtime/rowcount fingerprint the compiler recorded — a
    regenerated index invalidates the selection).

Increment A scope (2026-10-19): whole-file selection. Per-channel
restriction (channels_to_use) is increment B. Boundary tails are served
from the previous day's '2359' rows; January 1 is the one day the caller
may use the legacy tail query (prior year is outside this selection's
scope) — that use is recorded by the caller in the run manifest.
"""

import sqlite3
from pathlib import Path

KNOWN_SCHEMAS = {"selection-v0.1"}


class SelectionError(RuntimeError):
    """Selection artifact missing, truncated, mismatched, or unknown."""


def _manifest_identity(db_path: Path) -> str:
    import hashlib
    st = db_path.stat()
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    n = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    c.close()
    return hashlib.sha256(
        f"{db_path.name}:{st.st_size}:{int(st.st_mtime)}:{n}".encode()
    ).hexdigest()


class Selection:
    def __init__(self, path, net, sta, year, manifest_db_path):
        self.path = Path(path)
        if not self.path.is_file():
            raise SelectionError(f"selection not found: {self.path}")
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        meta = dict(self.conn.execute("SELECT key, value FROM meta"))
        if meta.get("schema_version") not in KNOWN_SCHEMAS:
            raise SelectionError(
                f"unknown selection schema {meta.get('schema_version')!r} "
                f"(known: {sorted(KNOWN_SCHEMAS)})")
        if meta.get("complete_marker") != "1":
            raise SelectionError(f"selection incomplete/truncated: {self.path}")
        for k, want in (("net", net), ("sta", sta), ("year", str(year))):
            if meta.get(k) != want:
                raise SelectionError(
                    f"selection {k}={meta.get(k)!r} != requested {want!r}")
        got = _manifest_identity(Path(manifest_db_path))
        if meta.get("manifest_identity") != got:
            raise SelectionError(
                "manifest identity mismatch: the index has changed since this "
                "selection was compiled — recompile the selection "
                f"(selection={meta.get('manifest_identity', '')[:12]}, "
                f"index={got[:12]})")
        self.meta = meta

    def days(self):
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT date FROM selection ORDER BY date")]

    def files_for_day(self, d_iso: str):
        """Ordered file paths selected for the day (all streams)."""
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT path FROM selection WHERE date=? ORDER BY path",
            (d_iso,))]

    def tail_for_day(self, prev_iso: str):
        """Previous day's final-minute rows (midnight-boundary Option C)."""
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT path FROM selection WHERE date=? AND hhmm='2359' "
            "ORDER BY path", (prev_iso,))]

    def close(self):
        self.conn.close()

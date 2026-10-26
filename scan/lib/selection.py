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

Boundary tails are served per channel-assignment group from the previous
day's final selected file(s) — see tail_for_day. January 1 takes its tail
from the PRIOR year's compiled selection (phase3 --prev-selection); the
legacy runtime tail query is never used in selection mode (review
2026-10-23 F3).
"""

import sqlite3
from pathlib import Path

# v0.1/v0.2 artifacts predate the month-wide probe evidence and the
# blocked-population record (review 2026-10-23 F4) — they are refused, not
# migrated: recompile with the current coverage_planner.
KNOWN_SCHEMAS = {"selection-v0.3"}


class SelectionError(RuntimeError):
    """Selection artifact missing, truncated, mismatched, or unknown."""


def _manifest_identity(db_path: Path) -> str:
    """Cheap per-day-job identity of the manifest DB. The compiler records
    it and every job re-checks it. The full-content binding is the
    compiler-recorded manifest_sha256, verified once per run via
    Selection(deep=True) (review 2026-10-23 F2)."""
    import hashlib
    st = db_path.stat()
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    n = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    c.close()
    return hashlib.sha256(
        f"{db_path.name}:{st.st_size}:{st.st_mtime_ns}:{n}".encode()
    ).hexdigest()


def _file_sha256(path, _bufsize=1 << 20):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


class Selection:
    def __init__(self, path, net, sta, year, manifest_db_path, deep=False):
        self.path = Path(path)
        if not self.path.is_file():
            raise SelectionError(f"selection not found: {self.path}")
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        meta = dict(self.conn.execute("SELECT key, value FROM meta"))
        if meta.get("schema_version") not in KNOWN_SCHEMAS:
            raise SelectionError(
                f"unknown selection schema {meta.get('schema_version')!r} "
                f"(known: {sorted(KNOWN_SCHEMAS)})")
        # Every provenance field is MANDATORY (Codex fix-review F2: a
        # stripped artifact omitting the hashes was accepted). Absence is
        # refusal, not leniency.
        REQUIRED = ("complete_marker", "net", "sta", "year",
                    "manifest_identity", "manifest_sha256", "generator_sha",
                    "generator_dirty", "probe_enabled", "probe_blocked",
                    "n_days", "n_rows")
        missing = [k for k in REQUIRED if meta.get(k) in (None, "")]
        if missing:
            raise SelectionError(
                f"selection missing required provenance field(s) {missing} "
                f"— not a complete artifact; recompile")
        if meta.get("complete_marker") != "1":
            raise SelectionError(f"selection incomplete/truncated: {self.path}")
        if meta.get("generator_dirty") != "False":
            raise SelectionError(
                "selection was compiled from a DIRTY working tree — its "
                "generator_sha does not describe the code that ran; "
                "commit and recompile")
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
        # Review 2026-10-23 F4: channel knowledge must be EVIDENCED. A
        # selection compiled with --no-probe assumed broadband Z/N/E; one
        # with probe-blocked populations has files whose channels nobody
        # could establish. Both refuse to convert.
        if meta.get("probe_enabled") != "True":
            raise SelectionError(
                "selection compiled with --no-probe (channel sets assumed, "
                "not evidenced) — recompile without --no-probe")
        import json as _json
        blocked = _json.loads(meta.get("probe_blocked") or "[]")
        if blocked:
            names = ", ".join(f"{b['population']} ({b['basis']}, "
                              f"{b['days']}d)" for b in blocked[:5])
            raise SelectionError(
                f"selection has {len(blocked)} probe-blocked population(s) "
                f"— channel content could not be established: {names}. "
                f"Investigate the probe errors in the probes table, then "
                f"recompile.")
        # Row/day counts must match what the compiler recorded (truncation
        # and tamper guard beyond the complete marker; review F2).
        n_rows = self.conn.execute(
            "SELECT COUNT(*) FROM selection").fetchone()[0]
        n_days = self.conn.execute(
            "SELECT COUNT(DISTINCT date) FROM selection").fetchone()[0]
        for key, got in (("n_rows", n_rows), ("n_days", n_days)):
            if int(meta[key]) != got:
                raise SelectionError(
                    f"selection {key}={got} != compiler-recorded {meta[key]} "
                    f"— artifact altered or truncated; recompile")
        if deep:
            self._deep_validate(Path(manifest_db_path), meta)
        self.meta = meta

    def _deep_validate(self, manifest_db_path: Path, meta: dict):
        """Once-per-run binding checks (review 2026-10-23 F2): full manifest
        content hash, generator == running code, and every selected row
        bound to the manifest.

        Result is CACHED in a sidecar keyed by the selection file's own
        content hash + the manifest's cheap identity: re-validating the
        same pair skips the expensive full-DB hash and row join (the study
        invokes phase3 per DAY; without this, a multi-GB station DB gets
        re-hashed hundreds of times — observed ~20 min/day, 2026-10-26).
        The sidecar only ever SHORT-CIRCUITS work that would succeed: it is
        trusted only when the selection bytes and manifest identity both
        still match what was validated."""
        import json as _json
        sidecar = self.path.with_name(self.path.name + ".deepok.json")
        sel_sha = _file_sha256(self.path)
        key = {"selection_sha256": sel_sha,
               "manifest_identity": meta.get("manifest_identity"),
               "generator_sha": meta.get("generator_sha")}
        try:
            if sidecar.is_file() and _json.loads(sidecar.read_text()) == key:
                return
        except Exception:
            pass
        want = meta.get("manifest_sha256")
        if want and _file_sha256(manifest_db_path) != want:
            raise SelectionError(
                "manifest CONTENT hash mismatch — the station DB's bytes "
                "differ from what this selection was compiled against; "
                "recompile the selection")
        import subprocess
        repo = Path(__file__).resolve().parent.parent.parent
        try:
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            head = ""
        gen = meta.get("generator_sha") or ""
        if head and gen and gen != head:
            raise SelectionError(
                f"selection compiled by a different chooser version "
                f"(generator {gen[:12]}, running {head[:12]}) — recompile "
                f"so evidence and code match")
        # Row-level binding (fix-review F2): every selected (path, date)
        # must name a manifest file OF THIS STATION dated to that day —
        # bare path membership would accept a path swapped for any other
        # file that merely exists somewhere in the index.
        sta = meta.get("sta")
        mc = sqlite3.connect(f"file:{manifest_db_path}?mode=ro", uri=True)
        try:
            bad, first_bad, batch = 0, None, []

            def _check(b):
                nonlocal bad, first_bad
                q = ",".join("?" * len(b))
                info = {r[0]: (r[1], f"{r[2]:04d}-{r[3]:02d}-{r[4]:02d}"
                               if r[2] else None)
                        for r in mc.execute(
                            f"SELECT path, station, file_year, file_month, "
                            f"file_day FROM files WHERE path IN ({q})",
                            [p for p, _ in b])}
                for p, d_iso in b:
                    got = info.get(p)
                    if got is None or got[0] != sta or (
                            got[1] is not None and got[1] != d_iso):
                        bad += 1
                        if first_bad is None:
                            first_bad = f"{p} (manifest: {got})"

            for p, d_iso in self.conn.execute(
                    "SELECT DISTINCT path, date FROM selection"):
                batch.append((p, d_iso))
                if len(batch) >= 500:
                    _check(batch)
                    batch = []
            if batch:
                _check(batch)
            if bad:
                raise SelectionError(
                    f"{bad} selected row(s) disagree with the manifest on "
                    f"existence, station or date (first: {first_bad}) — "
                    f"selection and index disagree; recompile")
        finally:
            mc.close()
        try:
            sidecar.write_text(_json.dumps(key))
        except OSError:
            pass  # read-only location: validation still ran, just uncached

    def days(self):
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT date FROM selection ORDER BY date")]

    def files_for_day(self, d_iso: str):
        """[(path, chans, rule)] selected for the day, WINNERS FIRST: fill
        rows (short_fill/gap_fill) sort after winner rows, and the converter
        preserves this order into the merge, whose equal-start tie break is
        input order — so the chooser's winner, not pathname or format,
        decides which samples survive an overlap (review 2026-10-23 F5).
        chans is the orientation set to take from that file."""
        return [(r[0], r[1] or "*", r[2] or "") for r in self.conn.execute(
            "SELECT path, chans, rule FROM selection WHERE date=? "
            "ORDER BY CASE WHEN rule LIKE 'short_fill%' "
            "OR rule LIKE 'gap_fill%' THEN 1 ELSE 0 END, path",
            (d_iso,))]

    def tail_for_day(self, prev_iso: str):
        """Previous day's FINAL selected file(s) per channel-assignment
        group, WITH their channel restrictions (midnight-boundary Option C).

        Review 2026-10-23 F3: returns [(path, chans)] — the caller must
        honour each tail's own channel assignment, not feed every tail to
        every channel group. Also not literally hhmm='2359': a day whose
        final coverage comes from an hourly/23-hour bundle has its last
        selected file at an earlier hhmm, so this takes the max-hhmm row(s)
        per chans group."""
        rows = self.conn.execute(
            "SELECT path, chans, hhmm, span_s FROM selection WHERE date=?",
            (prev_iso,)).fetchall()
        # Rank by estimated COVERAGE END, not raw hhmm: hour/day bundles are
        # stored with hhmm NULL (level1 convention) yet can carry the final
        # minutes of the day — a bare max-hhmm rule skipped them (Codex
        # fix-review F3). end = start-minute (NULL -> 0) + span in minutes.
        best = {}  # chans -> (max_end_min, {paths})
        for path, chans, hhmm, span_s in rows:
            ch = chans or "*"
            if hhmm and len(hhmm) == 4 and hhmm.isdigit():
                start_min = int(hhmm[:2]) * 60 + int(hhmm[2:])
            else:
                start_min = 0
            end_min = start_min + max(1.0, float(span_s or 60.0) / 60.0)
            cur = best.get(ch)
            if cur is None or end_min > cur[0]:
                best[ch] = (end_min, {path})
            elif end_min == cur[0]:
                cur[1].add(path)
        return sorted((p, ch) for ch, (_e, ps) in best.items() for p in ps)

    def close(self):
        self.conn.close()

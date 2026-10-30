#!/usr/bin/env python3
"""coverage_planner — compile identity + manifest into the exhaustive selection.

The decision core of workflow v3 (docs/rework_plan.md). For one
(net, sta, year) it decides, per target channel and day, exactly which
source files feed the converter — writing every decision and every
rejected alternative into a selection SQLite that the v3 converter treats
as its ONLY input (fail-closed; absence of a valid selection is a refusal
to convert).

v0.2 (per-channel selection — the Class-2 recovery):
  1. Populations: candidates grouped by (source_type × grammar variant);
     hour bundles are their own population, detected by filename.
  2. Channel knowledge per population: single-channel files carry their
     channel in the filename; multi-channel populations are header-PROBED
     once per (population, month) — one real file read — and the result
     cached in the selection DB (probes table). Probe failure falls back
     to the Z/N/E broadband assumption, recorded as such.
  3. Per-ORIENTATION winner: for each orientation, the population with
     the best measured minute-coverage that actually carries it wins the
     whole day (no alternation between overlapping time-shifted copies —
     the STBK guard); gap-fill from other populations only into missing
     minutes. A bundle covers its hour's 60 minute-slots.
  4. Each selected file records WHICH orientations to take from it
     (chans column) — the converter extracts only those. This is what
     lets a day take Z from disk and N/E from telemetry bundles without
     double-ingesting Z (build-order Step 5 semantics).
  5. Every rejected candidate is recorded with the reason.

Stray-day rule (operator 2026-10-18) is Increment C; not yet implemented
here — strays currently compile like any day and are caught downstream.

Schema selection-v0.2:
  meta(key, value)
  selection(date, stream, population, path, hhmm, span_s, role, rule,
            chans, PRIMARY KEY(date, stream, path, chans))
  rejected(date, stream, population, path, reason)
  probes(population, month, chans, probe_path)
  day_summary(date, stream, n_selected, n_rejected, n_gap_filled,
              populations_considered)
"""

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date as date_cls, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA_VERSION = "selection-v0.4"
HOURBUNDLE_RE = re.compile(r"[ _](\d{2})hr[ _]", re.IGNORECASE)
ORIENTATIONS = set("ZNEO123")  # SEED orientations incl. borehole 1/2/3
NS_ = 1_000_000_000
PRIORITY_OF = {"winner": 0, "boundary_tail": 0, "short_fill": 1,
               "gap_fill": 2}

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS selection (
    date TEXT, stream TEXT, population TEXT, path TEXT,
    hhmm TEXT, span_s REAL, role TEXT, rule TEXT, chans TEXT,
    PRIMARY KEY (date, stream, path, chans));
CREATE TABLE IF NOT EXISTS rejected (
    date TEXT, stream TEXT, population TEXT, path TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS probes (
    population TEXT, month TEXT, chans TEXT, basis TEXT,
    probe_paths TEXT, errors TEXT,
    PRIMARY KEY (population, month));
CREATE TABLE IF NOT EXISTS selected_file (
    file_id INTEGER PRIMARY KEY,
    date TEXT NOT NULL, stream TEXT NOT NULL, path TEXT NOT NULL,
    population TEXT NOT NULL, source_format TEXT NOT NULL,
    UNIQUE(date, stream, path));
CREATE TABLE IF NOT EXISTS component_decision (
    decision_id TEXT NOT NULL,
    file_id INTEGER NOT NULL REFERENCES selected_file(file_id),
    orientation TEXT NOT NULL,
    window_start_ns INTEGER NOT NULL,
    window_end_ns INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    selection_reason TEXT NOT NULL,
    PRIMARY KEY(decision_id, file_id, orientation,
                window_start_ns, window_end_ns));
CREATE INDEX IF NOT EXISTS cd_window ON component_decision
    (window_start_ns, window_end_ns);
CREATE TABLE IF NOT EXISTS file_channel_evidence (
    path TEXT NOT NULL,
    raw_channel TEXT NOT NULL,
    orientation TEXT,
    trace_count INTEGER NOT NULL,
    reader_status TEXT NOT NULL,
    evidence_method TEXT NOT NULL,
    PRIMARY KEY(path, raw_channel));
CREATE TABLE IF NOT EXISTS blocked_days (
    date TEXT, population TEXT, basis TEXT,
    PRIMARY KEY (date, population));
CREATE TABLE IF NOT EXISTS day_summary (
    date TEXT, stream TEXT, n_selected INT, n_rejected INT,
    n_gap_filled INT, populations_considered TEXT,
    PRIMARY KEY (date, stream));
"""


def git_sha(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


# Single implementation shared with the loader so compiler and converter
# can never drift apart (review 2026-10-23 F2).
from lib.selection import _manifest_identity as manifest_identity  # noqa: E402
from lib.selection import _file_sha256  # noqa: E402


def population_key(source_type: str, path: str, hhmm, flags) -> str:
    name = Path(path).name
    if (flags and "hour_bundle" in flags) or HOURBUNDLE_RE.search(name):
        return f"{source_type}:hour_bundle"
    if " " in name and "_" in name.split(".")[0]:
        return f"{source_type}:mixed_sep"
    if hhmm is None:
        return f"{source_type}:unparsed"
    sep = "space" if " " in name else "underscore"
    n_tok = len(name.split()) if " " in name else name.split(".")[0].count("_") + 1
    return f"{source_type}:{sep}{n_tok}"


def minute_slots(cand) -> set:
    """Minute keys a candidate file covers (a bundle covers its hour)."""
    name = Path(cand["path"]).name
    m = HOURBUNDLE_RE.search(name)
    if m:
        hh = m.group(1)
        return {f"{hh}{mm:02d}" for mm in range(60)}
    return {cand["hhmm"]} if cand["hhmm"] else set()


def load_day_candidates(db, day: date_cls, minimus: bool):
    rows = db.execute(
        "SELECT path, source_type, hhmm, ss, flags, exclude_reason, "
        "size_bytes, channel_suffix "
        "FROM files WHERE role='waveform' AND station_mismatch=0 "
        "AND file_year=? AND file_month=? AND file_day=?",
        (day.year, day.month, day.day)).fetchall()
    out = []
    for path, src, hhmm, ss, flags, excl, size, chan_suffix in rows:
        if excl is not None:
            if excl == "single_channel" and minimus and path.endswith(
                    (".mseed.zip", ".mseed")):
                pass
            elif excl in ("single_channel", "unparsed") and path.endswith(
                    (".mseed.zip", ".mseed", ".ms.zip", ".ms")):
                pass  # revived cohorts are full candidates in v0.2
            else:
                continue
        out.append({"path": path, "source": src, "hhmm": hhmm, "ss": ss,
                    "flags": flags, "size": size or 0,
                    "chan_suffix": chan_suffix,
                    "population": population_key(src, path, hhmm, flags)})
    return out


class ChannelKnowledge:
    """Which orientations a population carries, per month.

    Priority: single-channel filename suffix (exact, per file) →
    month-wide header-probe union → NOTHING. Review 2026-10-23 F4: probes
    sample the whole population-month (first, middle AND last files across
    all its days, not the first day encountered), every probe and error is
    persisted, and a population whose probes all fail is BLOCKED — the old
    silent broadband Z/N/E assumption invented channels. The assumption
    survives only under explicit --no-probe, which conversion refuses.

    One probe proved unsafe twice: SCM2 (first file was Z-only in a
    per-channel population) and TYHS (first disk file was a 1-channel
    sidecar in a mostly 3-channel population; 27 min/day of E+Z lost)."""

    N_FIRST = 2   # first files of the month
    N_MID = 1     # a mid-month file (catches mid-month config changes)
    N_LAST = 2    # rolling: always ends holding the month's final files

    def __init__(self, probe_enabled=True):
        self.samples = {}  # (pop, 'YYYY-MM') -> {"first": [], "mid": [], "last": []}
        self.all_paths = {}  # (pop, 'YYYY-MM') -> every unsuffixed path
        self.cache = {}    # (pop, 'YYYY-MM') -> (chans fset, basis, [paths], [errors])
        self.per_file = {}  # (pop, 'YYYY-MM') -> {path: frozenset(chans)}
        self.probe_enabled = probe_enabled
        self.sizes = {}  # (pop, 'YYYY-MM') -> {path: size_bytes}

    def observe(self, cand, month_key, day_of_month):
        """Pass 1: register an unsuffixed candidate as a probe sample."""
        key = (cand["population"], month_key)
        self.all_paths.setdefault(key, []).append(cand["path"])
        self.sizes.setdefault(key, {})[cand["path"]] = cand.get("size") or 0
        s = self.samples.setdefault(
            key, {"first": [], "mid": [], "last": []})
        if len(s["first"]) < self.N_FIRST:
            s["first"].append(cand["path"])
        if day_of_month >= 14 and len(s["mid"]) < self.N_MID:
            s["mid"].append(cand["path"])
        s["last"].append(cand["path"])
        if len(s["last"]) > self.N_LAST:
            s["last"].pop(0)

    def _probe_file(self, p):
        """Full per-file evidence (Codex resolver review F5):
        status is 'resolved' (recognized channels), 'empty' (no
        waveform traces at all), or 'unresolved' (read errors,
        or traces whose labels nobody recognizes) — unresolved
        NEVER silently rejects; it blocks the affected days."""
        from qa_verify import read_source_file
        ie, ic = [], {}
        rec = {"chans": frozenset(), "raw": [], "traces": 0,
               "status": "unresolved", "err": ""}
        try:
            found = set()
            for t in read_source_file(p, ie, ic):
                rec["traces"] += 1
                rec["raw"].append(t.get("src_chan", "?"))
                if t["orient"] in ORIENTATIONS:
                    found.add(t["orient"])
            rec["chans"] = frozenset(found)
            dropped = ic.get("dropped_labels") or []
            rec["raw"] += [f"?{c}" for c in dropped]
            if ie:
                rec["status"] = "unresolved"
                rec["err"] = str([e.get("err", str(e))
                                  for e in ie][:2])
            elif found:
                rec["status"] = "resolved"
            elif dropped or rec["traces"]:
                rec["status"] = "unresolved"  # labels unknown
            else:
                rec["status"] = "empty"  # no waveform content
        except Exception as e:
            rec["err"] = f"{type(e).__name__}: {e}"
        return p, rec

    def probe_all(self):
        """End of pass 1: probe every observed (population, month) once."""
        for key, s in sorted(self.samples.items()):
            if key in self.cache:
                continue
            paths = list(dict.fromkeys(s["first"] + s["mid"] + s["last"]))
            per_path, probed, errors, notes = {}, [], [], []
            if self.probe_enabled:
                from qa_verify import read_source_file
                for p in paths:
                    ierrs, ictrs = [], {}
                    try:
                        found = set()
                        for t in read_source_file(p, ierrs, ictrs):
                            if t["orient"] in ORIENTATIONS:
                                found.add(t["orient"])
                        per_path[p] = frozenset(found)
                        probed.append(p)
                        # Verdict-affecting: internal read errors (the file
                        # may carry channels the failed members hold).
                        if ierrs:
                            errors.append(
                                f"{p}: reader_errors="
                                f"{[e.get('err', str(e)) for e in ierrs][:3]}")
                        # Informational, NOT verdict-affecting: raw labels
                        # the reader saw but does not recognize (Codex item
                        # 2 — these make empty probes classifiable).
                        if ictrs.get("dropped_labels"):
                            notes.append(
                                f"{p}: unrecognized_labels={ictrs['dropped_labels']}")
                    except Exception as e:
                        errors.append(f"{p}: {type(e).__name__}: {e}")
            if not self.probe_enabled:
                self.cache[key] = (frozenset("ZNE"),
                                   "assumed_broadband_noprobe", [], [])
                continue
            # Codex fix-review F4: a month's channel set is trusted only
            # when every probe SUCCEEDED and they AGREE. Disagreement means
            # a mid-month configuration change — assigning the union would
            # credit early files with channels they don't carry (the Class-2
            # counterexample). Errors alongside successes mean the failing
            # files could be the ones carrying other channels. Both block.
            sets = set(per_path.values())
            agreed = next(iter(sets)) if len(sets) == 1 else None
            if probed and not errors and agreed:
                # SIZE-OUTLIER CHECK (2026-10-30, CLV2 2021): sampled probes
                # can agree on "Z only" while rare files in the month carry
                # three channels (8 one-minute 3-channel files per day at
                # CLV2 — their E/N were silently dropped). A file holding
                # more channels is several times larger, so every file well
                # above the month's median size is probed individually —
                # near-free (sizes are in the index; only outliers are read).
                sz = self.sizes.get(key, {})
                vals = sorted(v for v in sz.values() if v)
                med = vals[len(vals) // 2] if vals else 0
                outliers = [q for q, v in sz.items()
                            if med and v > 1.6 * med and q not in per_path]
                if outliers:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=8) as ex:
                        recs = list(ex.map(self._probe_file, outliers))
                    differing = [q for q, rec in recs
                                 if rec["chans"] != agreed
                                 or rec["status"] != "resolved"]
                    if differing:
                        full = {q: {"chans": agreed, "raw": [], "traces": -1,
                                    "status": "resolved", "err": ""}
                                for q in self.all_paths.get(key, [])}
                        for q, rec in recs:
                            full[q] = rec
                        self.per_file[key] = full
                        union = frozenset().union(
                            *(v["chans"] for v in full.values()))
                        self.cache[key] = (
                            union, "per_file_size_outliers", probed,
                            notes + [f"{len(differing)} size-outlier file(s) "
                                     f"differ from agreed "
                                     f"{''.join(sorted(agreed))}"])
                        continue
                self.cache[key] = (agreed, "header_probe_agreed", probed, notes)
            elif probed and (errors or len(sets) > 1):
                # ADAPTIVE PER-FILE EVIDENCE (Codex design B, 2026-10-27):
                # a disagreeing or error-bearing month means a population-
                # level channel fact is NOT a safe execution input (WPNH
                # mixes Z-only and ZNE files on the same day; TYHS mixes
                # 1-channel sidecars into a 3-channel population). Probe
                # EVERY unsuffixed file of this population-month and keep
                # the channel set per path — expensive, but confined to
                # the exceptional cohort. Files with no recognised
                # channels contribute nothing and are recorded.
                detail = [f"{p}: {''.join(sorted(s)) or '-'}"
                          for p, s in per_path.items()]

                # Re-probe EVERYTHING per-file (Codex launch list, probe
                # errors): a sampled probe that partially read could hide
                # channels; evidence must come from _probe_one's full
                # per-file record for every path in the month.
                full = {}
                todo = list(self.all_paths.get(key, []))
                # NFS-latency-bound single-file reads: 8 threads cut the
                # wall time ~6-8x (single-threaded, BEST's mostly-empty
                # cohort blew the study's 1 h compile budget, 2026-10-28).
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=8) as ex:
                    for p, rec in ex.map(self._probe_file, todo):
                        full[p] = rec
                        if rec["err"]:
                            errors.append(f"{p}: {rec['err']}")
                        unk = [c for c in rec["raw"] if c.startswith("?")]
                        if unk:
                            notes.append(f"{p}: unrecognized_labels={unk}")
                self.per_file[key] = full
                union = (frozenset().union(*(v["chans"] for v in full.values()))
                         if full else frozenset())
                self.cache[key] = (union, "per_file_probe", probed,
                                   detail[:10] + errors[:10] + notes[:10])
            else:
                self.cache[key] = (frozenset(), "probe_failed", probed,
                                   errors + notes)

    def channels(self, population, month_key):
        return self.cache.get((population, month_key),
                              (frozenset(), "unobserved", [], []))


def choose_day(cands, know: ChannelKnowledge, month_key):
    """Per-orientation population winner + minute-gap-fill.

    Channel knowledge is PER FILE: a filename that declares its channel is
    exact for that file; only non-declaring files inherit their
    population's probed channel set. A file is only ever selected for
    orientations it actually carries, and a population's coverage for an
    orientation counts only the files that carry it. (Fix for the SCM2
    2020-12-31 study failure: a per-channel population was sampled at
    3 files, concluded Z-only, and two channels were filtered away.)

    Returns (selected {path -> {chans, rule, cand}}, rejected list, n_gap,
    blocked [(population, basis)] — populations whose unsuffixed files have
    no evidenced channel set; review F4: these BLOCK the selection rather
    than being guessed)."""
    pops = defaultdict(list)
    for c in cands:
        pops[c["population"]].append(c)

    f_orients = {}
    blocked = set()
    for pk, files in pops.items():
        unsuffixed = [f for f in files
                      if not (f.get("chan_suffix")
                              and f["chan_suffix"][-1].upper() in ORIENTATIONS)]
        pop_set = frozenset()
        per_file = None
        if unsuffixed:
            pop_set, basis, _paths, _errs = know.channels(pk, month_key)
            if basis.startswith("per_file"):
                # Mixed cohort (WPNH/TYHS class): channel knowledge is
                # exact PER FILE; a file with no recognised channels
                # contributes nothing (recorded below), the rest convert.
                per_file = know.per_file.get((pk, month_key), {})
            elif not pop_set:
                blocked.add((pk, basis))
        for f in files:
            suf = f.get("chan_suffix")
            if suf and suf[-1].upper() in ORIENTATIONS:
                f_orients[f["path"]] = frozenset(suf[-1].upper())
            elif per_file is not None:
                rec = per_file.get(f["path"])
                if rec is None:
                    f_orients[f["path"]] = frozenset()
                elif rec["status"] == "unresolved":
                    # Codex F5: a candidate whose channel identity could
                    # not be established blocks ITS day — never a silent
                    # rejection, never the whole year.
                    blocked.add((pk, "file_unresolved"))
                    f_orients[f["path"]] = frozenset()
                else:
                    f_orients[f["path"]] = rec["chans"]
            else:
                f_orients[f["path"]] = pop_set

    rejected, n_gap = [], 0
    # Visibility: files that end up with NO channel evidence (empty probes,
    # unrecognised labels) are recorded as rejected, never silently skipped.
    for pk, files in pops.items():
        for f in files:
            if not f_orients.get(f["path"]):
                rejected.append(dict(f, reason=f"no_channel_evidence:{pk}"))

    all_orients = set().union(*f_orients.values()) if f_orients else set()
    sel = defaultdict(lambda: {"chans": set(), "rule": None, "cand": None})

    for orient in sorted(all_orients):
        # carriers: per population, only the FILES that carry this orientation
        carriers = {}
        for pk, files in pops.items():
            fs = [f for f in files if orient in f_orients[f["path"]]]
            if fs:
                carriers[pk] = fs
        if not carriers:
            continue

        def coverage(files):
            slots = set()
            for f in files:
                slots |= minute_slots(f)
            return slots

        ranked = sorted(
            ((pk, fs, coverage(fs)) for pk, fs in carriers.items()),
            key=lambda t: (len(t[2]),
                           0.5 if t[1][0]["source"] == "disk" else 0.0,
                           sum(f["size"] for f in t[1])),
            reverse=True)
        win_pk, win_files, covered = ranked[0]
        sizes = sorted(f["size"] for f in win_files if f["size"])
        med = sizes[len(sizes) // 2] if sizes else 0
        short_minutes = {f["hhmm"] for f in win_files
                         if f["hhmm"] and med and f["size"] < 0.88 * med}
        for f in win_files:
            e = sel[f["path"]]
            e["chans"].add(orient)
            e["rule"] = e["rule"] or f"winner:{orient}:{win_pk}"
            e.setdefault("by_orient", {})[orient] = f"winner:{orient}:{win_pk}"
            e.setdefault("o_slots", {}).setdefault(orient, set()).update(
                minute_slots(f))
            e["cand"] = f
        # Sub-minute gap-fill: a winner file well below its population's
        # median size is likely seconds short; also take the best other
        # copy of that minute so the merge can supply the tail (overlap
        # policy trims the duplicated span).
        if short_minutes:
            for pk, fs, _cov in ranked[1:]:
                for f in fs:
                    if f["hhmm"] in short_minutes:
                        e = sel[f["path"]]
                        e["chans"].add(orient)
                        e["rule"] = e["rule"] or f"short_fill:{orient}:{pk}"
                        e.setdefault("by_orient", {})[orient] = f"short_fill:{orient}:{pk}"
                        if f["hhmm"]:
                            e.setdefault("o_slots", {}).setdefault(
                                orient, set()).add(f["hhmm"])
                        e["cand"] = f
                        short_minutes.discard(f["hhmm"])
        for pk, fs, _cov in ranked[1:]:
            for f in fs:
                extra = minute_slots(f) - covered
                if extra:
                    e = sel[f["path"]]
                    e["chans"].add(orient)
                    e["rule"] = e["rule"] or f"gap_fill:{orient}:{pk}"
                    e.setdefault("by_orient", {})[orient] = f"gap_fill:{orient}:{pk}"
                    e.setdefault("o_slots", {}).setdefault(
                        orient, set()).update(extra)
                    e["cand"] = f
                    covered |= extra
                    n_gap += 1
                else:
                    rejected.append(dict(
                        f, reason=f"{orient}:population_lost_to:{win_pk}"))
    return sel, rejected, n_gap, sorted(blocked)


def compile_selection(net, sta, year, db_path: Path, out_path: Path,
                      registry: dict, repo_root: Path, probe=True):
    entry = registry.get(sta) or {}
    minimus = "minimus" in (entry.get("recorder_types") or [])
    loc = entry.get("target_location") or "00"
    stream_base = f"{net}.{sta}.{loc}"

    src_db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".building")
    if tmp.exists():
        tmp.unlink()
    out = sqlite3.connect(tmp)
    out.executescript(DDL)
    # Fix-review F2: a dirty working tree makes generator_sha a lie (it
    # records HEAD, not the edited code that actually ran). Refuse.
    dirty = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True, text=True).stdout.strip()
    dirty_code = [ln for ln in dirty.splitlines()
                  if not ln.startswith("??") or
                  ln[3:].strip('"').endswith((".py", ".sh", ".bash"))]
    if dirty_code:
        raise SystemExit(
            f"coverage_planner REFUSES to compile from a dirty working "
            f"tree ({len(dirty_code)} modified/untracked code file(s), "
            f"first: {dirty_code[0]}): commit first so generator_sha "
            f"describes the code that ran")
    meta = {
        "schema_version": SCHEMA_VERSION,
        "generator_sha": git_sha(repo_root),
        "generator_dirty": "False",
        "manifest_db": str(db_path),
        "manifest_identity": manifest_identity(db_path),
        # Full content hash of the manifest DB (review 2026-10-23 F2): the
        # cheap identity above is checked per day-job; this one is checked
        # once per phase3 run (deep validation) and cannot be satisfied by
        # a different database with the same size/mtime/rowcount.
        "manifest_sha256": _file_sha256(db_path),
        "net": net, "sta": sta, "year": str(year),
        "probe_enabled": str(bool(probe)),
        "created_utc": __import__("datetime").datetime.utcnow().isoformat(),
    }
    out.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", meta.items())

    know = ChannelKnowledge(probe_enabled=probe)
    # Pass 1 (review 2026-10-23 F4): register every unsuffixed candidate
    # across the WHOLE month, so probes sample first/middle/last files of
    # each (population, month) — not just the first day encountered — then
    # probe each once.
    d = date_cls(year, 1, 1)
    while d.year == year:
        for c in load_day_candidates(src_db, d, minimus):
            suf = c.get("chan_suffix")
            if not (suf and suf[-1].upper() in ORIENTATIONS):
                know.observe(c, f"{d.year}-{d.month:02d}", d.day)
        d += timedelta(days=1)
    know.probe_all()

    blocked_days = {}  # (population, basis) -> day count
    d = date_cls(year, 1, 1)
    n_days = n_sel = 0
    while d.year == year:
        cands = load_day_candidates(src_db, d, minimus)
        if cands:
            mk = f"{d.year}-{d.month:02d}"
            sel, rejected, gaps, blocked = choose_day(cands, know, mk)
            for b in blocked:
                blocked_days[b] = blocked_days.get(b, 0) + 1
            # Per-DAY blocked record (Codex targeted review item 3: one bad
            # population-month must not block the whole station-year — only
            # the days where that population actually offers files).
            out.executemany(
                "INSERT OR REPLACE INTO blocked_days VALUES (?,?,?)",
                [(d.isoformat(), pk, basis) for pk, basis in blocked])
            iso = d.isoformat()
            rows = []
            for path, e in sel.items():
                f = e["cand"]
                # Real covered minutes, not a one-hour guess: a 23-hour
                # bundle must out-rank a 23:59 minute in the tail lookup
                # when it truly carries the day's end (fix-review F3).
                span = 60.0 * max(1, len(minute_slots(f)))
                rows.append((iso, stream_base, f["population"], path,
                             f["hhmm"], span, "waveform", e["rule"],
                             "".join(sorted(e["chans"]))))
            out.executemany(
                "INSERT OR REPLACE INTO selection VALUES (?,?,?,?,?,?,?,?,?)",
                rows)
            # v0.4 normalized decisions (Codex resolver review F3): one row
            # per (file, orientation) with an authorized UTC window and an
            # explicit priority — the resolver's execution contract. The
            # final day's spill decision authorizes the boundary sliver of
            # the NEXT day explicitly (replacing the tail heuristics).
            import calendar as _cal
            day_ns = _cal.timegm(d.timetuple()) * NS_
            tail_best = {}  # orientation -> (window_end_ns, window_start_ns, file_id)
            for path, e in sel.items():
                f = e["cand"]
                out.execute(
                    "INSERT OR IGNORE INTO selected_file"
                    "(date, stream, path, population, source_format) "
                    "VALUES (?,?,?,?,?)",
                    (iso, stream_base, path, f["population"],
                     "suds" if path.endswith((".dmx", ".dmx.gz"))
                     else "mseed"))
                fid = out.execute(
                    "SELECT file_id FROM selected_file WHERE date=? AND "
                    "stream=? AND path=?",
                    (iso, stream_base, path)).fetchone()[0]
                for orient, reason in sorted(
                        (e.get("by_orient") or {}).items()):
                    # Windows are the GRANTED slots for this orientation
                    # (Codex launch list: fills were over-broad — a
                    # gap-fill's window is the gap it supplies, never the
                    # whole file), one decision row per contiguous run.
                    slots = sorted((e.get("o_slots") or {}).get(orient)
                                   or minute_slots(f))
                    runs = []
                    if slots:
                        mins = [int(s0[:2]) * 60 + int(s0[2:])
                                for s0 in slots]
                        a = b = mins[0]
                        for m in mins[1:]:
                            if m == b + 1:
                                b = m
                            else:
                                runs.append((a, b))
                                a = b = m
                        runs.append((a, b))
                    else:
                        runs = [(0, 1439)]
                    prio = PRIORITY_OF.get(reason.split(":")[0], 0)
                    for a, b in runs:
                        ws = day_ns + a * 60 * NS_
                        # One slot of grace at the window end: minute files
                        # STRADDLE their nominal slot (session offset SS —
                        # a file labelled 0001 holds 00:01:SS..00:02:SS).
                        # Clipping to the bare slot discarded the carry-over
                        # (TYHS 2026-10-29: fixed seconds lost per file).
                        # Benign overlap between consecutive same-priority
                        # files is dedup'd by the resolver's occupancy.
                        we = day_ns + (b + 2) * 60 * NS_
                        out.execute(
                            "INSERT OR REPLACE INTO component_decision "
                            "VALUES (?,?,?,?,?,?,?)",
                            (f"{iso}:{orient}", fid, orient, ws, we,
                             prio, reason))
                        if reason.startswith("winner") and \
                                (we, ws) >= tail_best.get(orient, (0, 0))[:2]:
                            # Midnight source = the winner decision ending
                            # latest, tie-broken by LATEST start (the file
                            # actually spanning 23:59), not path order.
                            tail_best[orient] = (we, ws, fid)
            next_iso = (d + timedelta(days=1)).isoformat()
            for orient, (_we, _ws, fid) in sorted(tail_best.items()):
                out.execute(
                    "INSERT OR REPLACE INTO component_decision "
                    "VALUES (?,?,?,?,?,?,?)",
                    (f"{next_iso}:{orient}", fid, orient,
                     day_ns + 86400 * NS_, day_ns + 86460 * NS_,
                     0, "boundary_tail"))
            out.executemany(
                "INSERT INTO rejected VALUES (?,?,?,?,?)",
                [(iso, stream_base, f["population"], f["path"], f["reason"])
                 for f in rejected])
            out.execute(
                "INSERT OR REPLACE INTO day_summary VALUES (?,?,?,?,?,?)",
                (iso, stream_base, len(rows), len(rejected), gaps,
                 json.dumps(sorted({c['population'] for c in cands}))))
            n_days += 1
            n_sel += len(rows)
        d += timedelta(days=1)
    out.executemany(
        "INSERT OR REPLACE INTO probes VALUES (?,?,?,?,?,?)",
        [(pk, mo, "".join(sorted(ch)), basis,
          json.dumps(paths), json.dumps(errs))
         for (pk, mo), (ch, basis, paths, errs) in know.cache.items()])
    # Per-file channel evidence (Codex resolver review F5): every exhaust-
    # ively probed file's raw labels, trace count and read status persist
    # reviewably, keyed by path (evidence predates selection, so no FK).
    ev_rows = []
    for (pk, mo), files_map in know.per_file.items():
        for path, rec in files_map.items():
            raws = rec.get("raw") or [""]
            for raw in sorted(set(raws))[:12]:
                orient = (raw[-1].upper()
                          if raw and not raw.startswith("?")
                          and raw[-1].upper() in ORIENTATIONS else None)
                ev_rows.append((path, raw, orient, rec.get("traces", -1),
                                rec["status"] + (f": {rec['err'][:120]}"
                                                 if rec.get("err") else ""),
                                "per_file_probe"))
    out.executemany(
        "INSERT OR REPLACE INTO file_channel_evidence VALUES (?,?,?,?,?,?)",
        ev_rows)
    # Review F4: populations with unsuffixed files but NO evidenced channel
    # set block conversion — recorded here; the loader refuses when non-empty.
    out.execute("INSERT OR REPLACE INTO meta VALUES ('probe_blocked', ?)",
                (json.dumps([{"population": pk, "basis": basis, "days": n}
                             for (pk, basis), n in sorted(blocked_days.items())]),))
    # Counts come FROM THE TABLE, never from loop counters: a day with
    # candidates but zero selected rows (fully blocked, or everything
    # rejected) made the visited-days counter disagree with the loader's
    # distinct-date count and refuse valid artifacts (LOCU/HOGN
    # 2026-10-27).
    n_days_tbl, n_rows_tbl = out.execute(
        "SELECT COUNT(DISTINCT date), COUNT(*) FROM selection").fetchone()
    out.execute("INSERT OR REPLACE INTO meta VALUES ('complete_marker','1')")
    out.execute("INSERT OR REPLACE INTO meta VALUES ('n_days', ?)",
                (str(n_days_tbl),))
    out.execute("INSERT OR REPLACE INTO meta VALUES ('n_rows', ?)",
                (str(n_rows_tbl),))
    n_dec = out.execute(
        "SELECT COUNT(*) FROM component_decision").fetchone()[0]
    out.execute("INSERT OR REPLACE INTO meta VALUES ('n_decisions', ?)",
                (str(n_dec),))
    out.commit()
    out.close()
    src_db.close()
    tmp.rename(out_path)
    return n_days, n_sel


def main():
    import yaml
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--net", required=True)
    ap.add_argument("--sta", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--db", required=True, help="station manifest DB")
    ap.add_argument("--out", required=True, help="selection sqlite path")
    ap.add_argument("--no-probe", action="store_true",
                    help="skip header probes (broadband ZNE assumed). "
                         "INSPECTION ONLY: the converter refuses a "
                         "selection compiled this way (review F4).")
    ap.add_argument("--repo-root",
                    default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    with open(Path(args.repo_root) / "metadata" / "station_registry.yaml") as f:
        registry = yaml.safe_load(f) or {}
    n_days, n_sel = compile_selection(
        args.net, args.sta, args.year, Path(args.db), Path(args.out),
        registry, Path(args.repo_root), probe=not args.no_probe)
    print(f"selection compiled: {n_days} days, {n_sel} file-selections -> {args.out}")


if __name__ == "__main__":
    main()

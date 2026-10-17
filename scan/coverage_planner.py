#!/usr/bin/env python3
"""coverage_planner — compile identity + manifest into the exhaustive selection.

The decision core of workflow v3 (docs/rework_plan.md). For one
(net, sta, year) it decides, per target stream and day, exactly which
source files feed the converter, and writes the decision — with rejected
alternatives — into a selection SQLite that the v3 converter treats as its
ONLY input (no runtime fallback; absence of a valid selection is a refusal
to convert).

Selection rules (v1 of the chooser, per the reviewed design):
  1. Populations: candidate files are grouped into populations by
     (source_type, grammar variant). One population per (stream, day) is
     chosen by SCORE = measured minute-coverage (file count as proxy at
     this layer; header-measured coverage refines later), with
     tie-break: disk > telemetry, then larger total bytes. No alternation:
     the winning population supplies the whole day for that stream.
  2. Gap-fill: minutes absent from the winning population may be filled
     from other populations ONLY into measured gaps (missing hhmm keys),
     never by overlaying overlapping time (the STBK B2 guard).
  3. Revived cohorts (hour bundles, per-channel mseed for non-Minimus)
     are candidates like any other population — flagged so the converter
     knows spans may exceed one minute.
  4. Every rejected candidate is recorded with the reason (audit trail).

Schema (selection.sqlite):
  meta(key, value)                     -- schema_version, generator_sha,
                                          registry_sha, manifest_db,
                                          manifest_identity, net, sta, year,
                                          created_utc, complete_marker
  selection(date, stream, population, path, hhmm, span_s, role,
            rule, PRIMARY KEY(date, stream, path))
  rejected(date, stream, population, path, reason)
  day_summary(date, stream, n_selected, n_rejected, n_gap_filled,
              populations_considered)

`stream` is the full target identity NET.STA.LOC.CHAN once identity
contracts land; until then it is NET.STA.LOC.<orientation> with channel
codes resolved downstream (naming is deliberately decoupled — capture
first, rename cheap, per the governing principle).

Status: skeleton under active build (2026-10-17 night). The population
split + scoring logic lands first; header-measured coverage scoring and
identity-contract integration follow. Not yet production.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date as date_cls, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA_VERSION = "selection-v0.1"

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS selection (
    date TEXT, stream TEXT, population TEXT, path TEXT,
    hhmm TEXT, span_s REAL, role TEXT, rule TEXT,
    PRIMARY KEY (date, stream, path));
CREATE TABLE IF NOT EXISTS rejected (
    date TEXT, stream TEXT, population TEXT, path TEXT, reason TEXT);
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


def manifest_identity(db_path: Path) -> str:
    """Content identity of the manifest DB this selection was compiled
    from: size + mtime + row count (cheap, detects regeneration)."""
    st = db_path.stat()
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    n = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    c.close()
    return hashlib.sha256(
        f"{db_path.name}:{st.st_size}:{int(st.st_mtime)}:{n}".encode()
    ).hexdigest()


def population_key(source_type: str, path: str, hhmm, flags) -> str:
    """Stable population identity: source_type × grammar variant."""
    name = Path(path).name
    if flags and "hour_bundle" in flags:
        return f"{source_type}:hour_bundle"
    if " " in name and "_" in name.split(".")[0]:
        return f"{source_type}:mixed_sep"
    if hhmm is None:
        return f"{source_type}:unparsed"
    sep = "space" if " " in name else "underscore"
    n_tok = len(name.split()) if " " in name else name.split(".")[0].count("_") + 1
    return f"{source_type}:{sep}{n_tok}"


def load_day_candidates(db, day: date_cls, minimus: bool):
    """Candidate rows for one day: in-scope waveform + revived cohorts.
    Mirrors qa_verify's revival semantics (single source of truth for this
    moves to the domain package; tracked)."""
    rows = db.execute(
        "SELECT path, source_type, hhmm, ss, flags, exclude_reason, size_bytes "
        "FROM files WHERE role='waveform' AND station_mismatch=0 "
        "AND file_year=? AND file_month=? AND file_day=?",
        (day.year, day.month, day.day)).fetchall()
    out = []
    for path, src, hhmm, ss, flags, excl, size in rows:
        revived = False
        if excl is not None:
            if excl == "single_channel" and minimus and path.endswith(
                    (".mseed.zip", ".mseed")):
                pass  # Minimus per-channel IS the data
            elif excl in ("single_channel", "unparsed") and path.endswith(
                    (".mseed.zip", ".mseed", ".ms.zip", ".ms")):
                revived = True
            else:
                continue  # triggered / wrong_station etc. stay excluded
        out.append({"path": path, "source": src, "hhmm": hhmm, "ss": ss,
                    "flags": flags, "size": size or 0, "revived": revived,
                    "population": population_key(src, path, hhmm, flags)})
    return out


def choose_day(cands: list[dict]) -> tuple[list, list, int]:
    """v1 chooser for one (day, stream-group): pick ONE population by
    coverage score, gap-fill from others into missing minutes only.
    Returns (selected, rejected, n_gap_filled)."""
    if not cands:
        return [], [], 0
    pops = defaultdict(list)
    for c in cands:
        pops[c["population"]].append(c)

    def score(pop_files):
        minutes = {f["hhmm"] for f in pop_files if f["hhmm"]}
        disk_bonus = 0.5 if pop_files[0]["source"] == "disk" else 0.0
        return (len(minutes), disk_bonus, sum(f["size"] for f in pop_files))

    ranked = sorted(pops.items(), key=lambda kv: score(kv[1]), reverse=True)
    winner_key, winner = ranked[0]
    selected = [dict(f, rule="population_winner") for f in winner]
    covered = {f["hhmm"] for f in winner if f["hhmm"]}
    rejected, gap_filled = [], 0
    for key, files in ranked[1:]:
        for f in files:
            if f["hhmm"] and f["hhmm"] not in covered:
                selected.append(dict(f, rule="gap_fill"))
                covered.add(f["hhmm"])
                gap_filled += 1
            else:
                rejected.append(dict(
                    f, reason=f"population_lost_to:{winner_key}"
                    if f["hhmm"] else "unparsed_overlap_risk"))
    return selected, rejected, gap_filled


def compile_selection(net, sta, year, db_path: Path, out_path: Path,
                      registry: dict, repo_root: Path):
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
    meta = {
        "schema_version": SCHEMA_VERSION,
        "generator_sha": git_sha(repo_root),
        "manifest_db": str(db_path),
        "manifest_identity": manifest_identity(db_path),
        "net": net, "sta": sta, "year": str(year),
        "created_utc": __import__("datetime").datetime.utcnow().isoformat(),
    }
    out.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", meta.items())

    d = date_cls(year, 1, 1)
    n_days = n_sel = 0
    while d.year == year:
        cands = load_day_candidates(src_db, d, minimus)
        if cands:
            selected, rejected, gaps = choose_day(cands)
            iso = d.isoformat()
            out.executemany(
                "INSERT OR REPLACE INTO selection VALUES (?,?,?,?,?,?,?,?)",
                [(iso, stream_base, f["population"], f["path"], f["hhmm"],
                  3600.0 if (f["flags"] and "hour_bundle" in f["flags"])
                  else 60.0, "waveform", f["rule"]) for f in selected])
            out.executemany(
                "INSERT INTO rejected VALUES (?,?,?,?,?)",
                [(iso, stream_base, f["population"], f["path"], f["reason"])
                 for f in rejected])
            out.execute(
                "INSERT OR REPLACE INTO day_summary VALUES (?,?,?,?,?,?)",
                (iso, stream_base, len(selected), len(rejected), gaps,
                 json.dumps(sorted({c['population'] for c in cands}))))
            n_days += 1
            n_sel += len(selected)
        d += timedelta(days=1)
    out.execute("INSERT OR REPLACE INTO meta VALUES ('complete_marker','1')")
    out.execute("INSERT OR REPLACE INTO meta VALUES ('n_days', ?)", (str(n_days),))
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
    ap.add_argument("--repo-root",
                    default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    with open(Path(args.repo_root) / "metadata" / "station_registry.yaml") as f:
        registry = yaml.safe_load(f) or {}
    n_days, n_sel = compile_selection(
        args.net, args.sta, args.year, Path(args.db), Path(args.out),
        registry, Path(args.repo_root))
    print(f"selection compiled: {n_days} days, {n_sel} files -> {args.out}")


if __name__ == "__main__":
    main()

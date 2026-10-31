#!/usr/bin/env python3
"""qa_verify — sample-level comparison of original archive files vs converted SDS.

Mode A auditor for the QA verification campaign
(docs/qa/verification_campaign_plan.md). For one (net, sta, date):

  1. locate source files via the Level-1 station DB (never a filesystem walk),
  2. read originals independently (sudspy for PC-SUDS, ObsPy for miniSEED) —
     deliberately does NOT import phase3_driver / suds_convert,
  3. read the converted SDS day-files (default: LT, read-only),
  4. trim both to [day, day+1) and compare.

Coverage semantics (population-aware):
  expected  = disk coverage + telemetry gap-fill (minutes with no disk file)
              — what the documented disk-preferred selection converts
  tele_extra = telemetry coverage beyond expected (duplicate minutes,
              hourly bundles, odd grammars)
  missing   = expected − converted        -> missing_* categories
  selection_loss = tele_extra − converted -> data only telemetry had and
              conversion dropped (Class-2 signature), quantified in seconds

Sample semantics: a converted sample is wrong only if NO source copy
matches it (duplicate telemetry copies routinely disagree with each other;
the conversion picked one).

Emits one JSON record per day. Read-only against origin NFS and the SDS
root; writes nothing itself.

Categories:
  missing_day, missing_channel, missing_interval_boundary,
  missing_interval_midday, sample_mismatch, timing_misalign,
  duplicated_data, selection_loss, selection_loss_suspect,
  seed_id_mismatch, phantom_data, conv_data_without_source,
  source_read_error, no_station_db, comparator_error
"""

import argparse
import gzip
import io
import json
import re
import sqlite3
import sys
import zipfile
from datetime import date as date_cls, timedelta
from pathlib import Path

import numpy as np
import yaml
from obspy import UTCDateTime, read as obspy_read

from sudspy import read_suds_stream

# --- constants ---------------------------------------------------------------

# Kelunji component mapping (EchoPro c01/c02/c03 per manual; Echo Up-T etc.).
SUDS_COMPONENT = {
    "c01": "N", "c02": "E", "c03": "Z",
    "north-t": "N", "east-t": "E", "up-t": "Z",
}
SUDS_DROP_SUFFIXES = ("-a",)
SUDS_DROP_PREFIXES = ("c04", "c05", "c06", "c07", "c08", "c09")

ORIENTATIONS = set("ZNEO123")  # SEED orientations incl. borehole 1/2/3

HHMM_RE = re.compile(r"[ _](\d{4})[ _]")


def seed_band(rate_hz: float) -> str:
    """Gecko band-code table (broadband column, by sample rate)."""
    if rate_hz >= 1000:
        return "F"
    if rate_hz >= 250:
        return "C"
    if rate_hz >= 80:
        return "H"
    if rate_hz >= 10:
        return "B"
    return "L"


import os as _os
# Fractional-sample alignment tolerance for comparing a converted trace
# against a source copy. 0.5 is the PHYSICAL bound, not a loosening
# (2026-10-27, LKHRT false-FAIL): output anchored at each segment's
# recorder-stated time (the as-timestamped ruling) legitimately sits up to
# half a sample off any other file's stated grid — a finer tolerance
# flagged correct drift-split output as timing_misalign and left its
# coverage "phantom". Real timestamp welds are still caught, by VALUE:
# comparison happens at nearest-sample alignment, and welded data places
# samples whole samples away from their stated times, so the values
# disagree (that is exactly how the LKHRT 6.47M-bad-sample defect was
# detected). Sub-half-sample offsets are unrepresentable on a sample grid
# and are not a finding.
SAMPLE_TOL = 0.5
# Probe cadence / read caps are env-overridable for exhaustive runs
# (triage study): QA_PROBE_EVERY=1 QA_EXTRA_CAP=999999 reads everything.
TELE_PROBE_EVERY_MIN = int(_os.environ.get("QA_PROBE_EVERY", "30"))
MAX_FINDINGS_PER_CAT = 10  # cap repeated findings per category per day
EXTRA_READ_CAP = int(_os.environ.get("QA_EXTRA_CAP", "300"))


# --- registry / DB resolution -------------------------------------------------

def load_registry(repo_root: Path) -> dict:
    with open(repo_root / "metadata" / "station_registry.yaml") as f:
        return yaml.safe_load(f) or {}


def find_db(db_dirs: list[Path], net: str, sta: str) -> Path | None:
    """First DB on the search path that actually has a `files` table."""
    for d in db_dirs:
        p = d / f"{net}.{sta}.db"
        if not p.is_file():
            continue
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            ok = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='files'").fetchone()
            c.close()
            if ok:
                return p
        except sqlite3.Error:
            continue
    return None


# --- station DB queries --------------------------------------------------------

def _rows(db: sqlite3.Connection, y: int, m: int, d: int, hhmm: str | None = None):
    q = ("SELECT path, recorder_type, source_type, hhmm, ss, exclude_reason "
         "FROM files WHERE role='waveform' AND station_mismatch=0 "
         "AND file_year=? AND file_month=? AND file_day=?")
    args = [y, m, d]
    if hhmm is not None:
        q += " AND hhmm=?"
        args.append(hhmm)
    rows = [dict(zip(("path", "recorder", "source", "hhmm", "ss", "excl"), r))
            for r in db.execute(q, args)]
    for r in rows:
        if not r["hhmm"]:  # fallback parse for grammars level1 didn't tokenise
            mt = HHMM_RE.search(Path(r["path"]).name)
            r["hhmm"] = mt.group(1) if mt else None
    return rows


REVIVABLE_EXCL = ("single_channel", "unparsed")
HOURBUNDLE_RE = re.compile(r"[ _](\d{2})hr[ _]", re.IGNORECASE)


def query_day_files(db, day: date_cls, minimus: bool) -> list[dict]:
    """In-scope rows plus revived excluded cohorts.

    r["revived"] is False for rows the v1 conversion would have used
    (exclude_reason NULL, or Minimus single_channel), True for excluded
    cohorts we still audit as source data the archive holds (RT130
    per-channel single_channel files, 'unparsed' grammars such as hourly
    bundles). Revived rows only ever feed the tele_extra/union view, so
    their coverage shows up as selection_loss, never missing_*."""
    rows = _rows(db, day.year, day.month, day.day)
    out = []
    for r in rows:
        if r["excl"] is None:
            r["revived"] = False
            out.append(r)
        elif minimus and r["excl"] == "single_channel" and r["path"].endswith(
                (".mseed.zip", ".mseed")):
            r["revived"] = False  # Minimus per-channel mseed IS the data
            out.append(r)
        elif r["excl"] in REVIVABLE_EXCL and r["path"].endswith(
                (".mseed.zip", ".mseed", ".ms.zip", ".ms")):
            r["revived"] = True
            out.append(r)
    return out


def query_boundary_tail(db, day: date_cls, minimus: bool) -> list[dict]:
    """Previous day's final minute-files (they spill SS seconds into `day`):
    23:59 minute files plus 23hr hourly bundles."""
    prev = day - timedelta(days=1)
    rows = _rows(db, prev.year, prev.month, prev.day)
    out = []
    for r in rows:
        name = Path(r["path"]).name
        hb = HOURBUNDLE_RE.search(name)
        if r["hhmm"] != "2359" and not (hb and hb.group(1) == "23"):
            continue
        if r["excl"] is None or (minimus and r["excl"] == "single_channel"):
            r["revived"] = False
            out.append(r)
        elif r["excl"] in REVIVABLE_EXCL:
            r["revived"] = True
            out.append(r)
    return out


def variant_key(path: str, source: str) -> str:
    """Coarse filename-grammar class, for the multi-variant day metric."""
    name = Path(path).name
    if HOURBUNDLE_RE.search(name):
        return "hour_bundle"
    has_space = " " in name
    n_us = name.split(".")[0].count("_")
    if has_space and n_us:
        return "mixed_sep"
    if has_space:
        return f"tele_{len(name.split()) - 1}tok"
    return f"disk_{n_us}us"


def split_populations(files: list[dict]) -> tuple[list, list, list]:
    """(disk, tele_fill, tele_extra): tele_fill = telemetry minutes with no
    disk file (the documented gap-fill); tele_extra = everything else
    telemetry (duplicate minutes, hourly bundles, revived excluded
    cohorts). Revived rows are never 'expected' — their coverage can only
    surface as selection_loss."""
    live = [f for f in files if not f.get("revived")]
    revived = [f for f in files if f.get("revived")]
    disk = [f for f in live if f["source"] == "disk"]
    disk_minutes = {f["hhmm"] for f in disk if f["hhmm"]}
    tele_fill, tele_extra = [], []
    for f in live:
        if f["source"] == "disk":
            continue
        if disk and (not f["hhmm"] or f["hhmm"] in disk_minutes):
            tele_extra.append(f)
        else:
            tele_fill.append(f)
    if not disk:
        tele_fill, tele_extra = tele_fill + tele_extra, []
    return disk, tele_fill, tele_extra + revived


# --- source readers ------------------------------------------------------------

# All SEED band letters in this archive's universe (B/S@50, H/E@100-200,
# C/D@250-800, F/G@1000+): the earlier [ESHB] set silently excluded the
# C band — VW's own 250 sps convention — so SUDS telemetry stubs labelled
# CHZ were invisible to chooser, engine AND auditor at once (found by the
# independent witness on BEST 2019, 2026-10-27; the F9 common-mode risk
# made real). Keep textually identical to disk_to_sds _SEED_SEISMO_RE.
SEED_SEISMO_RE = __import__("re").compile(r"^[FGCDHESB]H[ZNE123]$")


# Echo spelling variants seen in EqServer (2026-10-31): "Up" (RNDA),
# "Up T"/"East T"/"North T" (BRTS, PLMR) — Up is Z by SEED definition.
# Keep identical to disk_to_sds suds_convert.echo_orient.
_ECHO_BARE = {"up": "Z", "north": "N", "east": "E",
              # PC-SUDS one-letter component codes (HKER 2017 "v",
              # "v Trans"; HKER 2018 files pair short "v" with c03 = Z)
              "v": "Z", "n": "N", "e": "E"}


def _echo_orient(chan: str) -> str | None:
    c = re.sub(r"[\s_]+", "-", (chan or "").strip()).lower()
    for suffix in ("-t", "-trans"):  # "-T"/"Trans" = translational (velocity)
        if c.endswith(suffix):
            c = c[: -len(suffix)]
            break
    else:
        if "-" in c:
            return None  # e.g. Up-A (accelerometer) or anything unrecognised
        return _ECHO_BARE.get(c)
    if "-" in c:
        return None
    return _ECHO_BARE.get(c)


def is_suds_path(path: str) -> bool:
    """SUDS by extension, or a bare '.gz' whose content is SUDS (HKER 2017
    '2017-03-15 0000 00 HKER.gz': gzipped SUDS the reader used to skip as
    an unrecognised mseed member). Sniffs two decompressed bytes."""
    if path.endswith((".dmx", ".dmx.gz")):
        return True
    if path.endswith(".gz") and not path.endswith((".ms.gz", ".mseed.gz")):
        try:
            with gzip.open(path, "rb") as fh:
                return fh.read(2) == b"S6"
        except Exception:
            return False
    return False


def _suds_component(chan: str) -> str | None:
    c = (chan or "").strip().lower()
    if c in SUDS_COMPONENT:
        return SUDS_COMPONENT[c]
    if _echo_orient(chan):
        return _echo_orient(chan)
    if c.endswith(SUDS_DROP_SUFFIXES) or c.startswith(SUDS_DROP_PREFIXES):
        return None
    # Recognised accelerometer spellings ("Up-A", "Up A", "v Acc", "v Accel"):
    # intentionally dropped (Codex e41bebb policy) — skipped and recorded,
    # never an unknown label.
    if re.sub(r"[\s_]+", "-", c).endswith(("-a", "-acc", "-accel")):
        return None
    # Already-SEED-named seismometer channels inside SUDS (KORUM
    # EHE/EHN/EHZ mid-deployment rename) — mirrors the engine's
    # conservative passthrough (disk_to_sds dba4f10).
    if SEED_SEISMO_RE.match((chan or "").strip().upper()):
        return (chan or "").strip().upper()[-1]
    return "?" + chan  # unknown label — surfaces in triage


def _mseed_orientation(chan: str) -> str | None:
    c = (chan or "").strip().upper()
    if c and c[-1] in ORIENTATIONS:
        return c[-1]
    return None


def _looks_like_mseed(blob: bytes) -> bool:
    return len(blob) >= 64 and blob[6:7] in b"DRQM"


def read_source_file(path: str, errors: list, counters: dict) -> list[dict]:
    """Read one source file into trace dicts:
    {orient, src_chan, start (UTCDateTime), rate, data (np.int64)}."""
    traces = []
    try:
        if is_suds_path(path):
            st = read_suds_stream(path)
            for tr in st:
                orient = _suds_component(tr.stats.channel)
                if orient is None:
                    counters["n_suds_chans_dropped"] = counters.get(
                        "n_suds_chans_dropped", 0) + 1
                    # Keep the raw labels (Codex item 2: silently discarded
                    # labels made BEST's empty probes unclassifiable).
                    labels = counters.setdefault("dropped_labels", [])
                    if tr.stats.channel not in labels and len(labels) < 20:
                        labels.append(tr.stats.channel)
                    continue
                traces.append({
                    "orient": orient, "src_chan": tr.stats.channel,
                    "src_path": path,
                    "start": tr.stats.starttime,
                    "rate": float(tr.stats.sampling_rate),
                    "data": np.asarray(tr.data, dtype=np.int64),
                })
        else:
            raw = Path(path).read_bytes()  # one sequential NFS read
            if path.endswith(".gz"):
                raw = gzip.decompress(raw)
            members = [raw]
            if path.endswith(".zip"):
                members = []
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for name in zf.namelist():
                        if name.endswith("/"):
                            continue
                        members.append(zf.read(name))
            for blob in members:
                if not _looks_like_mseed(blob):
                    counters["n_members_skipped"] = counters.get(
                        "n_members_skipped", 0) + 1
                    continue
                try:
                    st = obspy_read(io.BytesIO(blob), format="MSEED")
                except Exception as exc:
                    errors.append({"path": path, "err": f"member: {exc}"})
                    continue
                for tr in st:
                    orient = _mseed_orientation(tr.stats.channel)
                    if orient is None:
                        # An mseed channel with no orientation letter is
                        # UNKNOWN (blocks), never an intentional drop.
                        labels = counters.setdefault("unknown_labels", [])
                        if tr.stats.channel not in labels and len(labels) < 20:
                            labels.append(tr.stats.channel)
                        continue
                    traces.append({
                        "orient": orient, "src_chan": tr.stats.channel,
                        "src_path": path,
                        "start": tr.stats.starttime,
                        "rate": float(tr.stats.sampling_rate),
                        "data": np.asarray(tr.data, dtype=np.int64),
                    })
    except Exception as exc:
        errors.append({"path": path, "err": str(exc)})
    return traces


def read_population(files: list[dict], errors: list, counters: dict) -> list[dict]:
    out = []
    for f in files:
        out.extend(read_source_file(f["path"], errors, counters))
    return out


# --- interval arithmetic --------------------------------------------------------

def to_intervals(traces, day_start, day_end) -> dict[str, list]:
    """Per-orientation merged (start, end) float-epoch intervals, clipped to
    the day window."""
    t0, t1 = float(day_start.timestamp), float(day_end.timestamp)
    by = {}
    for tr in traces:
        if tr["rate"] <= 0 or len(tr["data"]) == 0:
            continue
        s = float(tr["start"].timestamp)
        e = s + len(tr["data"]) / tr["rate"]
        s, e = max(s, t0), min(e, t1)
        if e > s:
            by.setdefault(tr["orient"], []).append((s, e, tr["rate"]))
    out = {}
    for orient, ivs in by.items():
        tol = 1.0 / max(r for _, _, r in ivs)
        ivs = sorted((s, e) for s, e, _ in ivs)
        merged = [list(ivs[0])]
        for s, e in ivs[1:]:
            if s <= merged[-1][1] + tol:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        out[orient] = [tuple(iv) for iv in merged]
    return out


def union(a: list, b: list, tol: float = 0.0) -> list:
    ivs = sorted(a + b)
    if not ivs:
        return []
    merged = [list(ivs[0])]
    for s, e in ivs[1:]:
        if s <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [tuple(iv) for iv in merged]


def subtract(a: list, b: list, min_len: float) -> list:
    """Interval list a − b, dropping slivers shorter than min_len."""
    out = []
    for s, e in a:
        segs = [(s, e)]
        for bs, be in b:
            nxt = []
            for ss, se in segs:
                if be <= ss or bs >= se:
                    nxt.append((ss, se))
                else:
                    if bs > ss:
                        nxt.append((ss, bs))
                    if be < se:
                        nxt.append((be, se))
            segs = nxt
        out.extend(seg for seg in segs if seg[1] - seg[0] >= min_len)
    return out


def total(ivs: list) -> float:
    return sum(e - s for s, e in ivs)


# --- sample comparison ----------------------------------------------------------

def topology_breaks(src_traces, conv_traces, lineages=None):
    """Value-independent weld detector, lineage-aware (Codex fix review
    2026-10-27 F1: rounding source starts let the 0.51-sample constant-
    data weld tile cleanly, and unselected alternative copies masked
    breaks in the selected lineage).

    Walks each converted trace's coverage frontier with UNROUNDED source
    positions on the converted grid. A source may continue the frontier
    when its stated position sits within half a sample of it. It may
    OVERLAP the frontier (the trim/fill mechanism) only when it comes
    from a DIFFERENT lineage: one recorder lineage writes sequential
    minutes, so a same-lineage file overlapping its own continuation is a
    weld by construction — no duration threshold. `lineages` maps source
    path -> lineage key (the selection's population), restricting tiling
    to SELECTED copies; without it, every source is its own lineage and
    overlap legitimacy cannot be masked by an unselected copy."""
    n_break = 0
    details = []

    def _note(why, ct, extra=""):
        if len(details) < 3:
            details.append(
                f"{why} conv@{ct['start']} n={len(ct['data'])} {extra}")

    for ct in conv_traces:
        rate = ct["rate"]
        c0 = float(ct["start"].timestamp)
        n = len(ct["data"])
        if n == 0 or rate <= 0:
            continue
        tiles = []
        for st_ in src_traces:
            if abs(st_["rate"] - rate) > 1e-6 or len(st_["data"]) == 0:
                continue
            path = st_.get("src_path")
            if lineages is not None and path not in lineages:
                continue  # unselected copy: cannot legitimize output bytes
            p = (float(st_["start"].timestamp) - c0) * rate
            if p + len(st_["data"]) <= -0.5 or p >= n - 0.5:
                continue
            lin = (lineages.get(path) if lineages is not None
                   else (path or id(st_)))
            tiles.append((p, len(st_["data"]), lin, st_["data"]))
        if not tiles:
            n_break += 1
            _note("no-selected-source", ct)
            continue
        tiles.sort(key=lambda t: t[0])
        # Two separate notions: FRONTIER is raw coverage (any selected
        # copy); the PRIMARY chain (cur_*) is the lineage whose grid the
        # trace is currently following. Duplicate whole-minute copies —
        # selected-normal in this archive (BEST 2019-06-09: 1,464 files
        # for 1,440 minutes) — extend coverage but never the chain, or
        # their couple-sample offsets would poison the next junction.
        broke = False
        frontier = None
        cur_lin = cur_start = cur_len = None
        placed = []  # (integer grid pos, data, lineage) of on-grid tiles

        def _same_lineage_diff(pos, data, lin):
            # Codex launch review F2: every same-lineage on-grid overlap is
            # payload-compared over its ENTIRE extent, including copies
            # fully contained in existing coverage. Returns the first
            # differing overlap range, or None.
            for q_pos, q_data, q_lin in placed:
                if q_lin != lin:
                    continue
                o_lo = max(pos, q_pos)
                o_hi = min(pos + len(data), q_pos + len(q_data))
                if o_hi <= o_lo:
                    continue
                a = np.asarray(data[o_lo - pos:o_hi - pos])
                b = np.asarray(q_data[o_lo - q_pos:o_hi - q_pos])
                if not np.array_equal(a, b):
                    return (o_lo, o_hi)
            return None

        for p, L, lin, tdata in tiles:
            if frontier is None:
                if p > 0.5:
                    broke = True  # trace starts before any source does
                    _note("starts-before-sources", ct, f"first tile at +{p:.2f} samples")
                    break
                frontier, cur_lin, cur_start, cur_len = p + L, lin, p, L
                if abs(p - round(p)) <= 0.25:
                    placed.append((int(round(p)), tdata, lin))
                continue
            if abs(p - round(p)) <= 0.25:
                bad = _same_lineage_diff(int(round(p)), tdata, lin)
                if bad:
                    broke = True
                    _note("ongrid-overlap-values-differ", ct,
                          f"samples [{bad[0]},{bad[1]}) lin={lin}")
                    break
            if p + L <= frontier - 0.5:
                if abs(p - round(p)) <= 0.25:
                    placed.append((int(round(p)), tdata, lin))
                continue  # fully behind coverage (payload verified above)
            cont = p - (cur_start + cur_len)  # junction vs the chain
            if abs(cont) <= 0.5:
                # Grid-consistent next file: the chain advances.
                cur_lin, cur_start, cur_len = lin, p, L
                frontier = max(frontier, p + L)
                if abs(p - round(p)) <= 0.25:
                    placed.append((int(round(p)), tdata, lin))
            elif cont < -0.5:
                frac = abs(cont - round(cont))
                if lin == cur_lin and frac <= 0.25:
                    # ON-GRID OVERLAP: claimed duplication of the same
                    # recorder stream (SGWU boundary packets; HKER
                    # re-dispatched windows). ACCEPTED ONLY when the
                    # overlapping PAYLOADS are byte-equal — then discarding
                    # a copy provably loses nothing (Codex F6, accepted
                    # residual). Differing values fail LOUDLY here: no
                    # match-any-copy rule can mask them.
                    placed.append((int(round(p)), tdata, lin))
                    cur_lin, cur_start, cur_len = lin, p, L
                    frontier = max(frontier, p + L)
                elif lin == cur_lin and p - cur_start <= 0.5 * cur_len:
                    # Duplicate copy of the chain's current minute slot
                    # (stated starts nearly coincide): coverage only.
                    frontier = max(frontier, p + L)
                elif lin != cur_lin:
                    # Cross-lineage overlap: the fill mechanism. It takes
                    # over as the chain.
                    cur_lin, cur_start, cur_len = lin, p, L
                    frontier = max(frontier, p + L)
                else:
                    # Same-lineage tail-sliver overlap, OFF-grid or deep:
                    # weld signature.
                    broke = True
                    _note("same-lineage-sliver", ct,
                          f"junction cont={cont:.2f} smp (frac {frac:.2f}), "
                          f"lin={lin}, chain_len={cur_len}")
                    break
            else:
                # Ahead of the chain: fine only if other copies already
                # covered the junction.
                if p <= frontier + 0.5:
                    cur_lin, cur_start, cur_len = lin, p, L
                    frontier = max(frontier, p + L)
                else:
                    broke = True  # uncovered span inside the trace
                    _note("uncovered-span", ct,
                          f"junction cont=+{cont:.2f} smp beyond chain, "
                          f"frontier gap={p - frontier:.2f} smp, lin={lin}")
                    break
        if broke or frontier is None or frontier < n - 0.5:
            n_break += 1
            if not broke:
                _note("coverage-short", ct,
                      f"frontier {0 if frontier is None else frontier:.1f} of {n}")
    return n_break, details


def compare_samples(src_traces, conv_traces, day_start, day_end):
    """Match-any-copy comparison: per converted trace, a sample counts as bad
    only if at least one source copy covered it and none agreed.
    Returns (n_compared, n_mismatch, n_misaligned, covered_intervals)."""
    t0, t1 = float(day_start.timestamp), float(day_end.timestamp)
    n_cmp = n_bad = n_misalign = 0
    covered = []
    src_sorted = sorted(src_traces, key=lambda t: float(t["start"].timestamp))
    for ct in conv_traces:
        c0 = float(ct["start"].timestamp)
        rate = ct["rate"]
        nconv = len(ct["data"])
        if nconv == 0 or rate <= 0:
            continue
        c1 = c0 + nconv / rate
        compared = np.zeros(nconv, dtype=bool)
        matched = np.zeros(nconv, dtype=bool)
        for srct in src_sorted:
            if abs(srct["rate"] - rate) > 1e-6:
                continue
            s0 = float(srct["start"].timestamp)
            s1 = s0 + len(srct["data"]) / rate
            if s0 >= c1:
                break
            lo = max(c0, s0, t0)
            hi = min(c1, s1, t1)
            if hi - lo < 1.0 / rate:
                continue
            off_f = (lo - s0) * rate
            coff_f = (lo - c0) * rate
            frac = abs((off_f - round(off_f)) - (coff_f - round(coff_f)))
            if min(frac, 1 - frac) > SAMPLE_TOL:
                n_misalign += 1
                continue
            si, ci = int(round(off_f)), int(round(coff_f))
            n = min(len(srct["data"]) - si, nconv - ci,
                    int(round((hi - lo) * rate)))
            if n <= 0:
                continue
            seg = slice(ci, ci + n)
            compared[seg] = True
            matched[seg] |= (srct["data"][si:si + n] == ct["data"][seg])
            covered.append((lo, lo + n / rate))
        n_cmp += int(compared.sum())
        n_bad += int((compared & ~matched).sum())
    return n_cmp, n_bad, n_misalign, covered


# --- converted (SDS) reader ------------------------------------------------------

def read_converted(sds_root: Path, net: str, sta: str, day: date_cls):
    jday = day.timetuple().tm_yday
    sta_dir = sds_root / str(day.year) / net / sta
    traces, chans, locs, nets, stas = [], set(), set(), set(), set()
    if not sta_dir.is_dir():
        return traces, chans, locs, nets, stas
    day_start = UTCDateTime(day.year, day.month, day.day)
    day_end = day_start + 86400
    for chan_dir in sorted(sta_dir.glob("*.D")):
        for f in sorted(chan_dir.glob(f"*.{day.year}.{jday:03d}")):
            st = obspy_read(str(f))
            st.trim(day_start, day_end - 1e-6, nearest_sample=False)
            for tr in st:
                if tr.stats.npts == 0:
                    continue
                chans.add(tr.stats.channel)
                locs.add(tr.stats.location)
                nets.add(tr.stats.network)
                stas.add(tr.stats.station)
                orient = _mseed_orientation(tr.stats.channel) or "?"
                traces.append({
                    "orient": orient, "chan": tr.stats.channel,
                    "start": tr.stats.starttime,
                    "rate": float(tr.stats.sampling_rate),
                    "data": np.asarray(tr.data, dtype=np.int64),
                })
    return traces, chans, locs, nets, stas


# --- main per-day verification -----------------------------------------------------

def _add(rec, category, counts, **kw):
    counts[category] = counts.get(category, 0) + 1
    if counts[category] <= MAX_FINDINGS_PER_CAT:
        rec["findings"].append({"category": category, **kw})


def load_selection_lineages(selection_path, day: date_cls):
    """{path: population} for the day AND the previous day (boundary
    tails), parsed directly from the selection sqlite with our own query —
    deliberately NOT via lib.selection, so the auditor's view of the
    selection shares no loader code with the converter (Codex fix review
    2026-10-27: topology must tile only from SELECTED copies, with
    lineage, independently parsed)."""
    prev = (day - timedelta(days=1)).isoformat()
    c = sqlite3.connect(f"file:{selection_path}?mode=ro", uri=True)
    try:
        return {r[0]: r[1] or "?" for r in c.execute(
            "SELECT path, population FROM selection WHERE date IN (?, ?)",
            (day.isoformat(), prev))}
    finally:
        c.close()


def verify_day(net: str, sta: str, day: date_cls, db_dirs, sds_root: Path,
               registry: dict, stratum: str = "uniform",
               selection_path=None) -> dict:
    rec = {"t": "day", "net": net, "sta": sta, "date": day.isoformat(),
           "stratum": stratum, "findings": [], "metrics": {}}
    if isinstance(db_dirs, (str, Path)):
        db_dirs = [Path(db_dirs)]
    try:
        lineages = (load_selection_lineages(selection_path, day)
                    if selection_path else None)
        return _verify_day(rec, net, sta, day, [Path(d) for d in db_dirs],
                           Path(sds_root), registry, lineages)
    except Exception as exc:
        rec["findings"].append({"category": "comparator_error",
                                "detail": f"{type(exc).__name__}: {exc}"})
        return rec


def _verify_day(rec, net, sta, day, db_dirs, sds_root, registry,
                lineages=None):
    counts: dict = {}
    entry = registry.get(sta) or {}
    rts = entry.get("recorder_types") or []
    minimus = "minimus" in rts
    exp_loc = entry.get("target_location") or "00"

    db_path = find_db(db_dirs, net, sta)
    if db_path is None:
        _add(rec, "no_station_db", counts,
             detail=f"no usable {net}.{sta}.db on search path")
        rec["metrics"]["verdict"] = "finding"
        return rec
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    files = query_day_files(db, day, minimus)
    tail = query_boundary_tail(db, day, minimus)
    db.close()

    day_start = UTCDateTime(day.year, day.month, day.day)
    day_end = day_start + 86400

    conv_traces, conv_chans, conv_locs, conv_nets, conv_stas = read_converted(
        sds_root, net, sta, day)
    conv_iv = to_intervals(conv_traces, day_start, day_end)

    rec["metrics"]["n_src_files"] = len(files)
    rec["metrics"]["conv_seconds"] = round(
        sum(total(v) for v in conv_iv.values()), 2)

    # --- read sources by population -------------------------------------------
    # The previous-day tail is read even when the day itself has no files:
    # its final minute spills SS seconds into this day (tail-only days).
    errors: list = []
    counters: dict = {}
    disk_f, fill_f, extra_f = split_populations(files)
    tail_disk, tail_fill, tail_extra = split_populations(tail)
    expected_tr = read_population(disk_f + fill_f + tail_disk + tail_fill,
                                  errors, counters)
    extra_f_all = extra_f + tail_extra
    if len(extra_f_all) > EXTRA_READ_CAP:
        # Even subsample; selection_loss becomes a lower bound.
        step = max(1, len(extra_f_all) // EXTRA_READ_CAP)
        sampled = extra_f_all[::step][:EXTRA_READ_CAP]
        counters["n_extra_unread"] = len(extra_f_all) - len(sampled)
        extra_f_all = sampled
    extra_tr = read_population(extra_f_all, errors, counters)

    if not files and not expected_tr and not extra_tr:
        if conv_traces:
            # LT bytes with no eqserver source: usually live-seedlink era.
            _add(rec, "conv_data_without_source", counts,
                 detail="no in-scope source files this day")
            rec["metrics"]["verdict"] = "finding"
        else:
            rec["metrics"]["verdict"] = "ok_empty"
        return rec

    if errors:
        for e in errors[:MAX_FINDINGS_PER_CAT]:
            rec["findings"].append({"category": "source_read_error", **e})
        counts["source_read_error"] = len(errors)
    rec["metrics"]["n_read_errors"] = len(errors)
    rec["metrics"].update(counters)
    rec["metrics"]["recorders"] = sorted({f["recorder"] for f in files})
    variants = sorted({variant_key(f["path"], f["source"]) for f in files})
    rec["metrics"]["variants"] = variants
    rec["metrics"]["multi_variant"] = len(variants) > 1

    exp_iv = to_intervals(expected_tr, day_start, day_end)
    extra_iv = to_intervals(extra_tr, day_start, day_end)
    rec["metrics"]["src_seconds"] = round(
        sum(total(v) for v in exp_iv.values()), 2)

    all_src = expected_tr + extra_tr

    # --- per-orientation coverage + sample comparison -------------------------
    per_chan = {}
    total_cmp = total_bad = total_misalign = 0
    t0 = float(day_start.timestamp)
    orients = set(exp_iv) | set(extra_iv) | set(conv_iv)
    for orient in sorted(orients):
        e_iv = exp_iv.get(orient, [])
        x_iv = extra_iv.get(orient, [])
        c_iv = conv_iv.get(orient, [])
        # Loss threshold at THIS orientation's rate (a slow aux channel must
        # not raise the bar for the seismic channels).
        o_rates = [t["rate"] for t in all_src if t["orient"] == orient
                   and t["rate"] > 0]
        o_rates += [t["rate"] for t in conv_traces if t["orient"] == orient
                    and t["rate"] > 0]
        min_len = 1.5 / max(o_rates, default=100.0)
        missing = subtract(e_iv, c_iv, min_len)
        sel_loss = subtract(subtract(x_iv, e_iv, min_len), c_iv, min_len)
        src_for = [t for t in all_src if t["orient"] == orient]
        conv_for = [t for t in conv_traces if t["orient"] == orient]
        n_cmp, n_bad, n_mis, covered = compare_samples(
            src_for, conv_for, day_start, day_end)
        phantom = subtract(c_iv, [(s - min_len, e + min_len)
                                  for s, e in covered], 1.0)
        total_cmp += n_cmp
        total_bad += n_bad
        total_misalign += n_mis
        pc = {
            "src_s": round(total(e_iv), 2), "conv_s": round(total(c_iv), 2),
            "missing_s": round(total(missing), 2),
            "sel_loss_s": round(total(sel_loss), 2),
            "phantom_s": round(total(phantom), 2),
            "n_cmp": n_cmp, "n_bad": n_bad,
        }
        pc["missing"] = [[round(s - t0, 3), round(e - t0, 3)]
                         for s, e in missing[:20]]
        per_chan[orient] = pc
        for s, e in missing:
            boundary = (s - t0) < 120 or (float(day_end.timestamp) - e) < 120
            cat = ("missing_interval_boundary" if boundary
                   else "missing_interval_midday")
            _add(rec, cat, counts, channel=orient,
                 detail=f"{round(e - s, 3)}s at day-offset {round(s - t0, 1)}s")
        if e_iv and not c_iv:
            _add(rec, "missing_channel", counts, channel=orient)
        if c_iv and not e_iv and not x_iv:
            _add(rec, "extra_channel", counts, channel=orient,
                 detail=f"{round(total(c_iv), 1)}s converted data on an "
                        f"orientation no source carries")
        if total(sel_loss) > 5.0:
            _add(rec, "selection_loss", counts, channel=orient,
                 detail=f"{round(total(sel_loss), 1)}s telemetry-only data "
                        f"not in converted output")
        if total(phantom) > 2.0:
            _add(rec, "phantom_data", counts, channel=orient,
                 detail=f"{round(total(phantom), 1)}s converted data not "
                        f"matched to any source sample window")
        if n_bad:
            _add(rec, "sample_mismatch", counts, channel=orient,
                 detail=f"{n_bad}/{n_cmp} samples differ (no source copy "
                        f"agrees)")
        n_topo, topo_details = topology_breaks(src_for, conv_for, lineages)
        if n_topo:
            _add(rec, "timing_topology", counts, channel=orient,
                 detail=f"{n_topo} converted trace(s) span a source "
                        f"timeline break no copy supports: "
                        f"{'; '.join(topo_details)}")
        conv_dur = sum(len(t["data"]) / t["rate"] for t in conv_for)
        if conv_dur > 1.05 * total(c_iv) and conv_dur - total(c_iv) > 60:
            _add(rec, "duplicated_data", counts, channel=orient,
                 detail=f"trace-seconds {round(conv_dur)} vs unique "
                        f"coverage {round(total(c_iv))}")
    if total_misalign:
        _add(rec, "timing_misalign", counts,
             detail=f"{total_misalign} trace overlaps misaligned by "
                    f">{SAMPLE_TOL} samples")
    rec["metrics"]["n_cmp"] = total_cmp
    rec["metrics"]["n_bad"] = total_bad

    if orients and not conv_traces:
        rec["findings"] = [f for f in rec["findings"]
                           if f["category"] not in ("missing_channel",)]
        _add(rec, "missing_day", counts)

    # --- channel-set probe of duplicate telemetry (cheap Class-2 sentinel) ----
    probe_src = sorted(extra_f, key=lambda f: f["hhmm"] or "")
    probe = probe_src[::max(1, TELE_PROBE_EVERY_MIN)][:48]
    probed_extra = {t["orient"]
                    for f in probe
                    for t in read_source_file(f["path"], [], {})}
    extra_orients = probed_extra - set(exp_iv)
    already = {f.get("channel") for f in rec["findings"]
               if f["category"] in ("selection_loss", "missing_channel")}
    if extra_orients - already:
        _add(rec, "selection_loss_suspect", counts,
             detail=f"duplicate telemetry carries orientations "
                    f"{sorted(extra_orients - already)} absent from expected "
                    f"sources")

    # --- SEED id checks ---------------------------------------------------------
    if conv_nets - {net}:
        _add(rec, "seed_id_mismatch", counts,
             detail=f"net {sorted(conv_nets)} != {net}")
    if conv_stas - {sta}:
        _add(rec, "seed_id_mismatch", counts,
             detail=f"sta {sorted(conv_stas)} != {sta}")
    if conv_locs - {exp_loc}:
        _add(rec, "seed_id_mismatch", counts,
             detail=f"loc {sorted(conv_locs)} != {exp_loc}")
    rates = {round(t["rate"]) for t in all_src if t["rate"] > 0}
    if len(rates) == 1 and conv_chans:
        band = seed_band(next(iter(rates)))
        wrong = {c for c in conv_chans if c[0] != band}
        if wrong:
            _add(rec, "seed_id_mismatch", counts,
                 detail=f"band: source rate {sorted(rates)} expects "
                        f"'{band}*', converted has {sorted(wrong)}")

    rec["per_chan"] = per_chan
    rec["finding_counts"] = {k: v for k, v in counts.items() if v > 0}
    rec["metrics"]["verdict"] = ("finding" if rec["finding_counts"] else "ok")
    return rec


# --- CLI -------------------------------------------------------------------------

DEFAULT_DB_DIRS = [
    "/mnt/seiscomp_staging/eqserver_provenance/VW/2026-06-10/station_dbs",
    "~/station_dbs",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--net", required=True)
    ap.add_argument("--sta", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db-dirs", nargs="*", default=DEFAULT_DB_DIRS)
    ap.add_argument("--sds-root", default="/mnt/seiscomp_archive")
    ap.add_argument("--selection", default=None,
                    help="selection sqlite for this station-year: restricts "
                         "the timing-topology check to SELECTED source "
                         "copies with lineage (independently parsed here)")
    ap.add_argument("--repo-root",
                    default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()

    registry = load_registry(Path(args.repo_root))
    db_dirs = [Path(d).expanduser() for d in args.db_dirs]
    rec = verify_day(args.net, args.sta, date_cls.fromisoformat(args.date),
                     db_dirs, Path(args.sds_root), registry,
                     selection_path=args.selection)
    json.dump(rec, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

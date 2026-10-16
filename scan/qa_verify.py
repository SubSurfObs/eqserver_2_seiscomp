#!/usr/bin/env python3
"""qa_verify — sample-level comparison of original archive files vs converted SDS.

Mode A auditor for the QA verification campaign
(docs/qa/verification_campaign_plan.md). For one (net, sta, date):

  1. locate source files via the Level-1 station DB (never a filesystem walk),
  2. read originals independently (sudspy for PC-SUDS, ObsPy for miniSEED) —
     deliberately does NOT import phase3_driver / suds_convert,
  3. read the converted SDS day-files (default: LT, read-only),
  4. trim both to [day, day+1) and compare: integer sample equality,
     missing/phantom intervals (boundary vs mid-day), channel presence,
     SEED codes, duration.

Emits one JSON record per day (written by the caller / driver). Read-only
against origin NFS and the SDS root; writes nothing itself.

Finding categories (see plan doc):
  missing_day, missing_channel, missing_interval_boundary,
  missing_interval_midday, sample_mismatch, timing_misalign,
  duplicated_data, selection_loss_suspect, seed_id_mismatch, phantom_data,
  conv_data_without_source, source_read_error, comparator_error
"""

import argparse
import gzip
import io
import json
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
# SUDS channels deliberately dropped by the conversion (accelerometer / aux).
SUDS_DROP_SUFFIXES = ("-a",)
SUDS_DROP_PREFIXES = ("c04", "c05", "c06", "c07", "c08", "c09")

ORIENTATIONS = set("ZNEO12")

# Gecko band-code table (broadband column, by sample rate).
def seed_band(rate_hz: float) -> str:
    if rate_hz >= 1000:
        return "F"
    if rate_hz >= 250:
        return "C"
    if rate_hz >= 80:
        return "H"
    if rate_hz >= 10:
        return "B"
    return "L"


SAMPLE_TOL = 0.01          # fractional-sample alignment tolerance
TELE_PROBE_EVERY_MIN = 30  # probe cadence for tele-duplicate channel check


# --- registry ----------------------------------------------------------------

def load_registry(repo_root: Path) -> dict:
    with open(repo_root / "metadata" / "station_registry.yaml") as f:
        return yaml.safe_load(f) or {}


# --- station DB --------------------------------------------------------------

def _rows(db: sqlite3.Connection, y: int, m: int, d: int, hhmm: str | None = None):
    q = ("SELECT path, recorder_type, source_type, hhmm, ss, exclude_reason "
         "FROM files WHERE role='waveform' AND station_mismatch=0 "
         "AND file_year=? AND file_month=? AND file_day=?")
    args = [y, m, d]
    if hhmm is not None:
        q += " AND hhmm=?"
        args.append(hhmm)
    return [dict(zip(("path", "recorder", "source", "hhmm", "ss", "excl"), r))
            for r in db.execute(q, args)]


def query_day_files(db, day: date_cls, minimus: bool) -> list[dict]:
    rows = _rows(db, day.year, day.month, day.day)
    out = []
    for r in rows:
        if r["excl"] is None:
            out.append(r)
        elif minimus and r["excl"] == "single_channel" and r["path"].endswith(
                (".mseed.zip", ".mseed")):
            out.append(r)  # Minimus per-channel mseed IS the data
    return out


def query_boundary_tail(db, day: date_cls, minimus: bool) -> list[dict]:
    """Previous day's 23:59 minute-files (they spill SS seconds into `day`)."""
    prev = day - timedelta(days=1)
    rows = _rows(db, prev.year, prev.month, prev.day, hhmm="2359")
    return [r for r in rows if r["excl"] is None
            or (minimus and r["excl"] == "single_channel")]


def select_disk_preferred(files: list[dict]) -> list[dict]:
    """Documented dedup: per minute, disk wins; telemetry fills disk-less
    minutes. Everything that is neither 'disk' nor 'telemetry' is kept."""
    disk_minutes = {f["hhmm"] for f in files if f["source"] == "disk"}
    out = []
    for f in files:
        if f["source"] == "telemetry" and f["hhmm"] in disk_minutes:
            continue
        out.append(f)
    return out


# --- source readers ----------------------------------------------------------

def _suds_component(chan: str) -> str | None:
    c = (chan or "").strip().lower()
    if c in SUDS_COMPONENT:
        return SUDS_COMPONENT[c]
    if c.endswith(SUDS_DROP_SUFFIXES) or c.startswith(SUDS_DROP_PREFIXES):
        return None
    return "?" + chan  # unknown label — surfaces in triage


def _mseed_orientation(chan: str) -> str | None:
    c = (chan or "").strip().upper()
    if c and c[-1] in ORIENTATIONS:
        return c[-1]
    return None


def read_source_file(path: str, errors: list) -> list[dict]:
    """Read one source file into trace dicts:
    {orient, src_chan, start (UTCDateTime), rate, data (np.int64)}."""
    traces = []
    try:
        if path.endswith((".dmx", ".dmx.gz")):
            st = read_suds_stream(path)
            for tr in st:
                orient = _suds_component(tr.stats.channel)
                if orient is None:
                    continue
                traces.append({
                    "orient": orient, "src_chan": tr.stats.channel,
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
                if not blob:
                    continue
                try:
                    st = obspy_read(io.BytesIO(blob))
                except Exception as exc:  # non-mseed member (metadata etc.)
                    errors.append({"path": path, "err": f"member: {exc}"})
                    continue
                for tr in st:
                    orient = _mseed_orientation(tr.stats.channel)
                    if orient is None:
                        continue
                    traces.append({
                        "orient": orient, "src_chan": tr.stats.channel,
                        "start": tr.stats.starttime,
                        "rate": float(tr.stats.sampling_rate),
                        "data": np.asarray(tr.data, dtype=np.int64),
                    })
    except Exception as exc:
        errors.append({"path": path, "err": str(exc)})
    return traces


# --- interval arithmetic -----------------------------------------------------

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


# --- sample comparison -------------------------------------------------------

def compare_samples(src_traces, conv_traces, day_start, day_end):
    """Exact integer comparison on overlapping windows.
    Returns (n_compared, n_mismatch, n_misaligned, covered_intervals)."""
    t0, t1 = float(day_start.timestamp), float(day_end.timestamp)
    n_cmp = n_bad = n_misalign = 0
    covered = []
    for ct in conv_traces:
        c0 = float(ct["start"].timestamp)
        rate = ct["rate"]
        for srct in src_traces:
            if abs(srct["rate"] - rate) > 1e-6:
                continue
            s0 = float(srct["start"].timestamp)
            s1 = s0 + len(srct["data"]) / rate
            c1 = c0 + len(ct["data"]) / rate
            lo = max(c0, s0, t0)
            hi = min(c1, s1, t1)
            if hi - lo < 1.0 / rate:
                continue
            off_f = (lo - s0) * rate
            coff_f = (lo - c0) * rate
            frac = abs(off_f - round(off_f) - (coff_f - round(coff_f)))
            if min(frac, 1 - frac) > SAMPLE_TOL:
                n_misalign += 1
                continue
            si, ci = int(round(off_f)), int(round(coff_f))
            n = min(len(srct["data"]) - si, len(ct["data"]) - ci,
                    int(round((hi - lo) * rate)))
            if n <= 0:
                continue
            a = srct["data"][si:si + n]
            b = ct["data"][ci:ci + n]
            n_cmp += n
            n_bad += int(np.count_nonzero(a != b))
            covered.append((lo, lo + n / rate))
    return n_cmp, n_bad, n_misalign, covered


# --- converted (SDS) reader --------------------------------------------------

def read_converted(sds_root: Path, net: str, sta: str, day: date_cls):
    """All converted traces for the station-day, trimmed to the day window.
    Returns (traces, chan_codes, loc_codes, net_codes)."""
    jday = day.timetuple().tm_yday
    sta_dir = sds_root / str(day.year) / net / sta
    traces, chans, locs, nets = [], set(), set(), set()
    if not sta_dir.is_dir():
        return traces, chans, locs, nets
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
                orient = _mseed_orientation(tr.stats.channel) or "?"
                traces.append({
                    "orient": orient, "chan": tr.stats.channel,
                    "start": tr.stats.starttime,
                    "rate": float(tr.stats.sampling_rate),
                    "data": np.asarray(tr.data, dtype=np.int64),
                })
    return traces, chans, locs, nets


# --- main per-day verification ------------------------------------------------

def verify_day(net: str, sta: str, day: date_cls, db_dir: Path, sds_root: Path,
               registry: dict, stratum: str = "uniform") -> dict:
    rec = {"t": "day", "net": net, "sta": sta, "date": day.isoformat(),
           "stratum": stratum, "findings": [], "metrics": {}}
    try:
        return _verify_day(rec, net, sta, day, db_dir, sds_root, registry)
    except Exception as exc:
        rec["findings"].append({"category": "comparator_error",
                                "detail": f"{type(exc).__name__}: {exc}"})
        return rec


def _verify_day(rec, net, sta, day, db_dir, sds_root, registry):
    entry = registry.get(sta) or {}
    rts = entry.get("recorder_types") or []
    minimus = "minimus" in rts
    exp_loc = entry.get("target_location") or "00"

    db = sqlite3.connect(str(db_dir / f"{net}.{sta}.db"))
    files = query_day_files(db, day, minimus)
    tail = query_boundary_tail(db, day, minimus)
    db.close()

    day_start = UTCDateTime(day.year, day.month, day.day)
    day_end = day_start + 86400

    conv_traces, conv_chans, conv_locs, conv_nets = read_converted(
        sds_root, net, sta, day)
    conv_iv = to_intervals(conv_traces, day_start, day_end)

    rec["metrics"]["n_src_files"] = len(files)
    rec["metrics"]["conv_seconds"] = round(
        sum(total(v) for v in conv_iv.values()), 2)

    if not files and not conv_traces:
        rec["metrics"]["verdict"] = "ok_empty"
        return rec
    if not files and conv_traces:
        # LT bytes with no eqserver source: usually live-seedlink era.
        rec["findings"].append({"category": "conv_data_without_source",
                                "detail": "no in-scope source files this day"})
        rec["metrics"]["verdict"] = "finding"
        return rec

    recorders = {f["recorder"] for f in files}
    rec["metrics"]["recorders"] = sorted(recorders)

    # --- read selected sources (disk-preferred) + previous-day tail ----------
    errors: list = []
    selected = select_disk_preferred(files)
    src_traces = []
    for f in selected + select_disk_preferred(tail):
        src_traces.append((f, read_source_file(f["path"], errors)))
    flat_src = [t for _, ts in src_traces for t in ts]
    for e in errors[:20]:
        rec["findings"].append({"category": "source_read_error", **e})
    rec["metrics"]["n_read_errors"] = len(errors)

    sel_iv = to_intervals(flat_src, day_start, day_end)
    rec["metrics"]["src_seconds"] = round(
        sum(total(v) for v in sel_iv.values()), 2)

    # --- channel-set probe of unselected telemetry (Class-2 detector) --------
    disk_minutes = {f["hhmm"] for f in files if f["source"] == "disk"}
    tele_dup = sorted(
        (f for f in files
         if f["source"] == "telemetry" and f["hhmm"] in disk_minutes),
        key=lambda f: f["hhmm"])
    probe = tele_dup[::max(1, TELE_PROBE_EVERY_MIN)][:48]
    sel_orients = set(sel_iv)
    tele_extra = set()
    for f in probe:
        for t in read_source_file(f["path"], errors):
            if t["orient"] not in sel_orients:
                tele_extra.add(t["orient"])
    if tele_extra:
        rec["findings"].append({
            "category": "selection_loss_suspect",
            "detail": f"unselected telemetry carries orientations "
                      f"{sorted(tele_extra)} absent from selected sources",
        })

    # --- per-orientation coverage + sample comparison ------------------------
    min_rate = min((t["rate"] for t in flat_src if t["rate"] > 0), default=100.0)
    min_len = 2.0 / min_rate
    per_chan = {}
    total_cmp = total_bad = total_misalign = 0
    for orient, s_iv in sel_iv.items():
        c_iv = conv_iv.get(orient, [])
        missing = subtract(s_iv, c_iv, min_len)
        src_for = [t for t in flat_src if t["orient"] == orient]
        conv_for = [t for t in conv_traces if t["orient"] == orient]
        n_cmp, n_bad, n_mis, covered = compare_samples(
            src_for, conv_for, day_start, day_end)
        phantom = subtract(c_iv, [(s - min_len, e + min_len)
                                  for s, e in covered], 1.0)
        total_cmp += n_cmp
        total_bad += n_bad
        total_misalign += n_mis
        per_chan[orient] = {
            "src_s": round(total(s_iv), 2), "conv_s": round(total(c_iv), 2),
            "missing_s": round(total(missing), 2),
            "phantom_s": round(total(phantom), 2),
            "n_cmp": n_cmp, "n_bad": n_bad,
        }
        t0 = float(day_start.timestamp)
        for s, e in missing[:50]:
            boundary = (s - t0) < 120 or (float(day_end.timestamp) - e) < 120
            per_chan[orient].setdefault("missing", []).append(
                [round(s - t0, 3), round(e - t0, 3)])
            cat = ("missing_interval_boundary" if boundary
                   else "missing_interval_midday")
            rec["findings"].append({
                "category": cat, "channel": orient,
                "detail": f"{round(e - s, 3)}s at day-offset {round(s - t0, 1)}s"})
        if not c_iv and s_iv:
            rec["findings"].append({"category": "missing_channel",
                                    "channel": orient})
        if total(phantom) > 2.0:
            rec["findings"].append({
                "category": "phantom_data", "channel": orient,
                "detail": f"{round(total(phantom), 1)}s converted data not "
                          f"matched to any selected source sample window"})
        if n_bad:
            rec["findings"].append({
                "category": "sample_mismatch", "channel": orient,
                "detail": f"{n_bad}/{n_cmp} samples differ"})
        # duration ratio on the converted side (duplication detector)
        conv_dur = sum(len(t["data"]) / t["rate"] for t in conv_for)
        if conv_dur > 1.05 * total(c_iv) and conv_dur - total(c_iv) > 60:
            rec["findings"].append({
                "category": "duplicated_data", "channel": orient,
                "detail": f"trace-seconds {round(conv_dur)} vs unique "
                          f"coverage {round(total(c_iv))}"})
    if total_misalign:
        rec["findings"].append({
            "category": "timing_misalign",
            "detail": f"{total_misalign} trace overlaps misaligned by "
                      f">{SAMPLE_TOL} samples"})
    rec["metrics"]["n_cmp"] = total_cmp
    rec["metrics"]["n_bad"] = total_bad

    if sel_iv and not conv_traces:
        rec["findings"] = [f for f in rec["findings"]
                           if f["category"] != "missing_channel"]
        rec["findings"].append({"category": "missing_day"})

    # --- SEED id checks -------------------------------------------------------
    if conv_nets - {net}:
        rec["findings"].append({"category": "seed_id_mismatch",
                                "detail": f"net {sorted(conv_nets)} != {net}"})
    if conv_locs - {exp_loc}:
        rec["findings"].append({
            "category": "seed_id_mismatch",
            "detail": f"loc {sorted(conv_locs)} != {exp_loc}"})
    rates = {round(t["rate"]) for t in flat_src if t["rate"] > 0}
    if len(rates) == 1 and conv_chans:
        band = seed_band(next(iter(rates)))
        wrong = {c for c in conv_chans if c[0] != band}
        if wrong:
            rec["findings"].append({
                "category": "seed_id_mismatch",
                "detail": f"band: source rate {sorted(rates)} expects "
                          f"'{band}*', converted has {sorted(wrong)}"})

    rec["per_chan"] = per_chan
    rec["metrics"]["verdict"] = "finding" if rec["findings"] else "ok"
    return rec


# --- CLI ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--net", required=True)
    ap.add_argument("--sta", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--db-dir", default="~/station_dbs")
    ap.add_argument("--sds-root", default="/mnt/seiscomp_archive")
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()

    registry = load_registry(Path(args.repo_root))
    rec = verify_day(args.net, args.sta, date_cls.fromisoformat(args.date),
                     Path(args.db_dir).expanduser(), Path(args.sds_root),
                     registry)
    json.dump(rec, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()

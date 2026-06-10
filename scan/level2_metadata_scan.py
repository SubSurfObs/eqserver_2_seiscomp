"""level2_metadata_scan.py — emit reference/waveform_db/<net>_observations.yaml.

Walks the EqServer archive for the requested stations, samples representative
headers monthly within each plan-defined epoch, deduplicates into value-tuples,
derives proposed_epochs, and writes a YAML output matching the schema spec at
handoffs/uom_seismic_metadata/2026-06-10_waveform-sweep-schema/.

v1 scope (calibration):
- Monthly sampling within each plan_generator-defined epoch (no bisection yet)
- PC-SUDS header reader (sudspy.scan_suds_file)
- MiniSEED header reader (obspy headonly)
- Recorder identity from registry's recorder_types when single-valued;
  from PC-SUDS recorder code when EchoPro; else `unknown`
- Sensor identity from PC-SUDS sensor_type code (operator_input);
  else `unknown` for mseed (no .ss reader yet)
- Dedup observations by (recorder, sensor, sample_rate, gain) tuple
- Derive proposed_epochs by collapsing consecutive same-tuple observations

Out of scope for v1:
- Bisection within plan epochs (plan boundaries are trusted)
- Gecko `.ss` sidecar reader
- Full VW sweep (calibration set only: TRPU + OUTU by default)

Usage:
    python3 scan/level2_metadata_scan.py \\
        --network VW \\
        --stations TRPU,OUTU \\
        --plans-dir /mnt/seiscomp_staging/eqserver_sweep/plans/VW \\
        --registry metadata/station_registry.yaml \\
        --archive-root /mnt/eqserver_archive/shared/data/repository/archive \\
        --out vw_observations.yaml
"""
from __future__ import annotations
import argparse
import datetime as _dt
import json
import os
import socket
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DISK_TO_SDS_DEFAULT = Path("/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/scripts")


# === PC-SUDS code lookup tables ============================================
# 1-char codes embedded in STATIONCOMP. Lookup table derived from sudspy +
# Kelunji documentation. Incomplete coverage — flag any code not in table.

PCSUDS_RECORDER_CODE = {
    "K": "src/echopro",       # Kelunji EchoPro
    "E": "src/echo",          # Kelunji Echo (pre-EchoPro)
    # other codes flagged unknown + raw in catalogue_gap
}

# PC-SUDS sensor codes are operator-typed — the same code can mean different
# sensors per deployer convention. We keep the raw code + flag operator_input.
# A small high-confidence subset:
PCSUDS_SENSOR_CODE = {
    "G": "guralp/cmg-6t-1",   # VW Guralp 1 Hz, most common
    "T": "nanometrics/trillium-compact-20",
    # others flagged with raw code and catalogue_gap
}


# === Plan-epoch loader =====================================================

def load_plan(plans_dir: Path, sta: str) -> dict:
    p = plans_dir / f"VW.{sta}.plan.yaml"
    if not p.exists():
        p = plans_dir / f"{sta}.plan.yaml"  # fallback shape
    if not p.exists():
        return {}
    with p.open() as f:
        return yaml.safe_load(f) or {}


def load_registry_entry(registry_path: Path, sta: str) -> dict:
    with registry_path.open() as f:
        reg = yaml.safe_load(f) or {}
    return reg.get(sta, {})


# === File selection ========================================================

def list_station_year_months(archive_root: Path, sta: str) -> list[tuple[int, int]]:
    """List (year, month) tuples that have any data for the station.

    Excludes 1900/01/01 (the bogus-date dir CLAUDE.md describes).
    """
    sta_dir = archive_root / sta / "continuous"
    if not sta_dir.exists():
        return []
    out = []
    for y_dir in sorted(sta_dir.iterdir()):
        if not y_dir.is_dir():
            continue
        try:
            y = int(y_dir.name)
        except ValueError:
            continue
        if y < 2001:  # bogus-date sentinel
            continue
        for m_dir in sorted(y_dir.iterdir()):
            if not m_dir.is_dir():
                continue
            try:
                m = int(m_dir.name)
            except ValueError:
                continue
            out.append((y, m))
    return out


def _pick_file_in_dir(d_dir: Path) -> Optional[Path]:
    """Helper: pick one file from a single day-directory. Prefers PC-SUDS."""
    if not d_dir.is_dir():
        return None
    for ext in [".dmx", ".dmx.gz"]:
        for f in sorted(d_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name.endswith(ext) and ".trig" not in f.name:
                return f
    for ext in [".ms.zip", ".ms"]:
        for f in sorted(d_dir.iterdir()):
            if not f.is_file():
                continue
            if (f.name.endswith(ext)
                    and "_CHZ" not in f.name and "_CHN" not in f.name
                    and "_CHE" not in f.name):
                return f
    return None


def pick_representative_file(archive_root: Path, sta: str,
                             year: int, month: int) -> Optional[Path]:
    """Pick one file from the first day of the month that has any data."""
    m_dir = archive_root / sta / "continuous" / str(year) / f"{month:02d}"
    if not m_dir.exists():
        return None
    for d_dir in sorted(m_dir.iterdir()):
        f = _pick_file_in_dir(d_dir)
        if f is not None:
            return f
    return None


def pick_file_for_day(archive_root: Path, sta: str,
                      day: _dt.date,
                      search_radius: int = 3) -> Optional[Path]:
    """Pick one file for a specific day. If that day has no data, walks
    outwards up to `search_radius` days (preferring later days first since
    a transition typically establishes new state going forward).

    Returns (file_path, actual_day_used) or (None, None).
    """
    candidate_days = [day]
    for off in range(1, search_radius + 1):
        candidate_days.append(day + _dt.timedelta(days=off))
        candidate_days.append(day - _dt.timedelta(days=off))
    for d in candidate_days:
        d_dir = (archive_root / sta / "continuous"
                 / str(d.year) / f"{d.month:02d}" / f"{d.day:02d}")
        f = _pick_file_in_dir(d_dir)
        if f is not None:
            return f, d
    return None, None


# === Header readers ========================================================

def read_pcsuds_header(path: Path, disk_to_sds: Path) -> dict:
    """Read STATIONCOMP fields via sudspy. Returns a dict suitable for
    dropping into an observation's `values:` block (with separate
    bookkeeping fields like sample_count etc. populated elsewhere)."""
    sys.path.insert(0, str(disk_to_sds))
    try:
        sys.path.insert(0, "/home/unimelb.edu.au/dsand/projects/SubSurfObs/sudspy")
        from sudspy import scan_suds_file
        from sudspy.io import iter_suds_blocks
        from sudspy.parsers import parse_stationcomp_struct
    except ImportError as e:
        return {"_read_error": f"sudspy import failed: {e}"}

    # Need the STATIONCOMP guts, not just scan_suds_file (which strips them)
    stationcomp = None
    descriptrace = None
    for block in iter_suds_blocks(str(path), skip_data=True, strict=False):
        if block.struct_type == 5 and stationcomp is None:
            try:
                stationcomp = parse_stationcomp_struct(block)
            except Exception:
                pass
        elif block.struct_type == 7 and descriptrace is None:
            try:
                from sudspy.parsers import parse_descriptrace_struct
                descriptrace = parse_descriptrace_struct(block)
            except Exception:
                pass
        if stationcomp and descriptrace:
            break

    out = {}
    if descriptrace:
        out["sample_rate"] = float(descriptrace["struct_body"]["rate"])
    if stationcomp:
        sb = stationcomp["struct_body"]
        rec_code = (sb.get("recorder") or "").strip("\x00 ")
        sens_code = (sb.get("sensor_type") or "").strip("\x00 ")
        out["recorder_raw_code"] = rec_code
        out["sensor_raw_code"] = sens_code
        out["recorder"] = PCSUDS_RECORDER_CODE.get(rec_code, "unknown")
        out["sensor"] = PCSUDS_SENSOR_CODE.get(sens_code, "unknown")
        # PC-SUDS sentinel for "no value" is -32767. Emit as 'unknown'
        # (string sentinel per schema) instead of leaking the int.
        gain_raw = sb.get("atod_gain")
        if gain_raw == -32767 or gain_raw is None or gain_raw == 0:
            out["gain"] = "unknown"
        else:
            out["gain"] = int(gain_raw)
        out["max_gain"] = float(sb.get("max_gain") or 0.0)
        out["con_mvolts"] = float(sb.get("con_mvolts") or 0.0)
        out["st_lat"] = float(sb.get("st_lat") or 0.0)
        out["st_long"] = float(sb.get("st_long") or 0.0)
        out["elev"] = float(sb.get("elev") or 0.0)
        if stationcomp.get("statident"):
            si = stationcomp["statident"]
            out["network_code_seen"] = (si.get("network") or "").strip("\x00 ") or "unknown"
    return out


def read_gecko_ss(path: Path) -> dict:
    """Parse a Gecko kelunjimeta .ss sidecar (plain-text "key"=value
    format). Returns a dict of extracted fields, plus a parsed
    settings_time as ISO datetime (from the in-file field, not the
    filename — they typically match).
    """
    out: dict = {}
    if not path.exists():
        return {"_read_error": "file missing"}
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return {"_read_error": f"read failed: {e}"}

    # Format: lines like  "key"="string"  or  "key"=number  or  "key"=x1 (ro)
    import re
    pat = re.compile(r'^\s*"([^"]+)"=("?)([^"]*)\2(?:\s.*)?$')
    for line in text.splitlines():
        m = pat.match(line)
        if not m:
            continue
        k, _q, v = m.groups()
        out[k] = v.strip()

    # Parse settings_time "YYYY-MM-DD HHMM SS" into ISO datetime
    st = out.get("settings_time", "")
    if st:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2})(\d{2})\s+(\d{2})$", st)
        if m:
            d, h, mn, s = m.groups()
            out["settings_time_iso"] = f"{d}T{h}:{mn}:{s}Z"
    return out


def gecko_ss_config_tuple(ss: dict) -> tuple:
    """The config-fingerprint tuple for an .ss snapshot. Distinct values
    of this tuple are epoch-boundary candidates."""
    return (
        ss.get("serial", "unknown"),
        ss.get("cpv", "unknown"),
        ss.get("current_gain", "unknown"),
        ss.get("sampling_rate", "unknown"),
        ss.get("sensor_name", "unknown"),
        ss.get("firmware_version", "unknown"),
    )


def scan_gecko_ss(archive_root: Path, sta: str) -> list[dict]:
    """Walk all .ss files for the station, parse, dedup by config-tuple,
    return a chronologically-sorted list of distinct config observations.
    Each entry is one observation suitable for inclusion in the
    station's observations[] block.
    """
    ss_dir = archive_root / sta / "continuous" / "1900" / "01" / "01"
    if not ss_dir.exists():
        return []
    ss_files = sorted(p for p in ss_dir.iterdir()
                      if p.is_file() and p.name.endswith(".ss"))
    if not ss_files:
        return []

    parsed = []
    for p in ss_files:
        d = read_gecko_ss(p)
        if d.get("_read_error") or not d.get("settings_time_iso"):
            continue
        d["_path"] = p
        parsed.append(d)
    parsed.sort(key=lambda d: d["settings_time_iso"])

    # Dedup by config-tuple, chronologically
    dedup = []
    cur = None
    for d in parsed:
        t = gecko_ss_config_tuple(d)
        if cur is None or gecko_ss_config_tuple(cur) != t:
            d["_first_seen_iso"] = d["settings_time_iso"]
            d["_last_seen_iso"] = d["settings_time_iso"]
            d["_sample_count"] = 1
            dedup.append(d)
            cur = d
        else:
            cur["_last_seen_iso"] = d["settings_time_iso"]
            cur["_sample_count"] += 1

    # Convert into observations matching the schema shape
    obs = []
    for d in dedup:
        path_short = (d["_path"].name).strip()
        # Try to coerce sample_rate to int/float
        sr = d.get("sampling_rate", "")
        try:
            sr_val = float(sr)
        except (ValueError, TypeError):
            sr_val = None
        # gain like "x1 (ro)" → 1
        cg = d.get("current_gain", "")
        import re
        gm = re.match(r"x(\d+)", cg)
        gain_val = int(gm.group(1)) if gm else "unknown"
        obs.append({
            "source": f"gecko_ss/{sta}/{path_short}",
            "source_type": "gecko_ss",
            "sampled_at": d["settings_time_iso"][:10],  # ISO date
            "authority": "authoritative",
            "authority_overrides": {"sensor": "operator_input"},
            "recorder": "src/gecko",
            "recorder_serial": d.get("serial", "unknown"),
            "sensor": "unknown",  # raw sensor_name preserved separately
            "sensor_raw_name": d.get("sensor_name", ""),
            "sample_rate": sr_val,
            "gain": gain_val,
            "cpv": d.get("cpv", ""),
            "firmware_version": d.get("firmware_version", ""),
            "build_number": d.get("build_number", ""),
            "location_seen": (d.get("location_id", "").strip() or "00"),
            "channels_seen": [],  # .ss carries storing_chan flags, not codes
            "network_code_seen": d.get("network_code", "unknown"),
            "boundary_pinned": True,  # .ss settings_time is a precise pin
            "flags": ["suspicious_unknown_sensor"],  # sensor_name is operator_input
            "first_seen": d["_first_seen_iso"][:10],
            "last_seen": d["_last_seen_iso"][:10],
            "sample_count": d["_sample_count"],
        })
    return obs


def read_mseed_header(path: Path) -> dict:
    """Read miniSEED first record's blockette via obspy headonly.

    Handles `.ms.zip` (zip-wrapped single mseed) and bare `.ms`/`.mseed`.
    """
    try:
        from obspy import read
        import io as _io
    except ImportError as e:
        return {"_read_error": f"obspy import failed: {e}"}

    try:
        if path.suffix == ".zip" or path.name.endswith(".ms.zip"):
            import zipfile
            with zipfile.ZipFile(path) as z:
                members = [n for n in z.namelist() if not n.endswith("/")]
                if not members:
                    return {"_read_error": "empty zip"}
                with z.open(members[0]) as f:
                    data = f.read()
                st = read(_io.BytesIO(data), format="MSEED", headonly=True)
        else:
            st = read(str(path), format="MSEED", headonly=True)
    except Exception as e:
        return {"_read_error": f"obspy read failed: {e}"}

    if not st:
        return {"_read_error": "empty stream"}

    tr = st[0]
    out = {
        "sample_rate": float(tr.stats.sampling_rate),
        "network_code_seen": (tr.stats.network or "").strip() or "unknown",
        "channels_seen": sorted({t.stats.channel for t in st}),
        "location_seen": (tr.stats.location or "00").strip() or "00",
        "recorder": "unknown",      # mseed doesn't carry recorder identity
        "sensor": "unknown",        # nor sensor
        "gain": 1,                   # default — refine from registry/cohort
        "recorder_raw_code": None,
        "sensor_raw_code": None,
    }
    return out


# === Observation + epoch derivation ========================================

def value_tuple(obs: dict) -> tuple:
    """The response-determining 4-tuple + location, per the handoff schema."""
    return (
        obs.get("recorder", "unknown"),
        obs.get("sensor", "unknown"),
        obs.get("sample_rate"),
        obs.get("gain", 1),
        obs.get("location_seen", "00"),
    )


def _read_one_day(archive_root: Path, sta: str, day: _dt.date,
                  disk_to_sds: Path, reg_recorder_singleton: Optional[str]) -> Optional[tuple]:
    """Read one representative file for a single day. Returns the
    value_tuple or None if unable to sample.

    `reg_recorder_singleton` is the registry's recorder_types if single-
    valued, used to fill in `recorder` for mseed reads that can't
    determine it from bytes alone.
    """
    f, actual_day = pick_file_for_day(archive_root, sta, day)
    if f is None:
        return None
    if f.name.endswith(".dmx") or f.name.endswith(".dmx.gz"):
        hdr = read_pcsuds_header(f, disk_to_sds)
    else:
        hdr = read_mseed_header(f)
    if hdr.get("_read_error"):
        return None
    if reg_recorder_singleton and hdr.get("recorder") == "unknown":
        hdr["recorder"] = reg_recorder_singleton
    return value_tuple({
        "recorder": hdr.get("recorder", "unknown"),
        "sensor": hdr.get("sensor", "unknown"),
        "sample_rate": hdr.get("sample_rate"),
        "gain": hdr.get("gain", 1),
        "location_seen": hdr.get("location_seen", "00"),
    })


def bisect_transition(archive_root: Path, sta: str,
                      date_lo: _dt.date, date_hi: _dt.date,
                      tuple_lo: tuple, tuple_hi: tuple,
                      disk_to_sds: Path,
                      reg_recorder_singleton: Optional[str]) -> _dt.date:
    """Binary search for the first day with `tuple_hi`. Returns the pinned
    date. Falls back to `date_hi` (the month-level boundary we already
    had) if we hit unresolvable middle-ground or no-files day.
    """
    iters = 0
    while (date_hi - date_lo).days > 1 and iters < 10:
        iters += 1
        mid = date_lo + (date_hi - date_lo) / 2
        t = _read_one_day(archive_root, sta, mid, disk_to_sds,
                          reg_recorder_singleton)
        if t is None:
            # Couldn't sample at mid — bias toward earlier resolution
            # (assume change happened later than mid)
            date_lo = mid
            continue
        if t == tuple_lo:
            date_lo = mid
        elif t == tuple_hi:
            date_hi = mid
        else:
            # Third value at mid — transient mid-epoch change. Stop and
            # report the conservative boundary (date_hi); v2 doesn't
            # handle 3+ states gracefully.
            return date_hi
    return date_hi


def dedup_observations(raw_obs: list[dict]) -> list[dict]:
    """Collapse time-adjacent same-tuple observations into single entries
    with first_seen/last_seen/sample_count.

    raw_obs is a list sorted chronologically; each dict has a `sampled_at`
    ISO date plus value fields.
    """
    if not raw_obs:
        return []
    deduped = []
    cur = None
    for obs in raw_obs:
        t = value_tuple(obs)
        if cur is None or value_tuple(cur) != t:
            cur = dict(obs)
            cur["first_seen"] = obs["sampled_at"]
            cur["last_seen"] = obs["sampled_at"]
            cur["sample_count"] = 1
            # Collect raw codes for the audit trail
            cur["_raw_codes"] = []
            if obs.get("recorder_raw_code"):
                cur["_raw_codes"].append(("recorder", obs["recorder_raw_code"]))
            if obs.get("sensor_raw_code"):
                cur["_raw_codes"].append(("sensor", obs["sensor_raw_code"]))
            deduped.append(cur)
        else:
            cur["last_seen"] = obs["sampled_at"]
            cur["sample_count"] += 1
    return deduped


def derive_proposed_epochs(deduped: list[dict]) -> list[dict]:
    """Build proposed_epochs[] from deduplicated observations.

    Each dedup'd observation becomes one epoch. Boundary-pin labels
    use `archive_first_data`, `open`, `pinned_at_<YYYY-MM-DD>`, or
    `unpinned_data_gap` (when there's a long gap after this epoch
    where we have no observations).

    Confidence is:
      - `high` when both ends are bisection-pinned (or
        archive_first_data / open).
      - `medium` when at least one end is unpinned/gap-only.
      - `low` when the observation also carries sparse_observation
        flag (e.g. 2 samples over 13 years).
    """
    epochs = []
    for i, obs in enumerate(deduped):
        is_first = i == 0
        is_last = i == len(deduped) - 1
        has_gap_after = obs.get("_gap_after", False)
        start_pinned = obs.get("boundary_pinned", False) or is_first
        end_pinned = is_last or (
            not has_gap_after
            and i + 1 < len(deduped)
            and deduped[i + 1].get("boundary_pinned", False)
        )
        is_sparse = "sparse_observation" in obs.get("flags", [])
        if is_sparse:
            confidence = "low"
        elif start_pinned and end_pinned:
            confidence = "high"
        else:
            confidence = "medium"
        # Epoch end: actual last_seen when there's a gap (we don't know
        # how long the tuple persisted past last sample); next obs's
        # first_seen otherwise (the bisection-pinned transition).
        if is_last:
            epoch_end = None
            end_label = "open"
        elif has_gap_after:
            epoch_end = obs["last_seen"]
            end_label = f"unpinned_data_gap_after_{obs['last_seen']}"
        else:
            epoch_end = deduped[i + 1]["first_seen"]
            end_label = f"pinned_at_{deduped[i+1]['first_seen']}"
        ep = {
            "start": obs["first_seen"],
            "end": epoch_end,
            "recorder": obs.get("recorder", "unknown"),
            "sensor": obs.get("sensor", "unknown"),
            "sample_rate": obs.get("sample_rate"),
            "gain": obs.get("gain", 1),
            "location": obs.get("location_seen", "00"),
            "confidence": confidence,
            "boundary_pin": {
                "start": ("archive_first_data" if is_first
                          else f"pinned_at_{obs['first_seen']}"),
                "end": end_label,
            },
            "observations": obs.get("sample_count", 1),
            "flags": (["sparse_observation"] if is_sparse else []),
        }
        epochs.append(ep)
    return epochs


# === Per-station scan ======================================================

def scan_station(
    sta: str,
    archive_root: Path,
    plans_dir: Path,
    registry_path: Path,
    disk_to_sds: Path,
) -> dict:
    """Scan one station. Returns a dict ready to drop into the YAML's
    `stations:` block."""
    reg = load_registry_entry(registry_path, sta)
    plan = load_plan(plans_dir, sta)

    print(f"\n[scan] {sta}", file=sys.stderr)
    print(f"  registry recorder_types: {reg.get('recorder_types')}", file=sys.stderr)
    print(f"  plan epochs: {len(plan.get('epochs', []))}", file=sys.stderr)

    year_months = list_station_year_months(archive_root, sta)
    print(f"  archive year-months with data: {len(year_months)}", file=sys.stderr)
    if not year_months:
        return {"_no_data": True}

    raw_obs = []
    skipped = 0
    flags_aggregate = set()
    network_codes_seen = set()
    locations_seen = set()

    for (y, m) in year_months:
        f = pick_representative_file(archive_root, sta, y, m)
        if f is None:
            skipped += 1
            continue
        # Pick reader by extension
        if f.name.endswith(".dmx") or f.name.endswith(".dmx.gz"):
            hdr = read_pcsuds_header(f, disk_to_sds)
        else:
            hdr = read_mseed_header(f)

        if hdr.get("_read_error"):
            skipped += 1
            print(f"    skip {y}-{m:02d}: {hdr['_read_error']}", file=sys.stderr)
            continue

        # Recorder reconciliation against registry
        reg_recs = reg.get("recorder_types") or []
        if isinstance(reg_recs, list) and len(reg_recs) == 1 and hdr.get("recorder") == "unknown":
            hdr["recorder"] = reg_recs[0]

        obs = {
            "source": f"{'pcsuds' if f.name.endswith(('.dmx','.dmx.gz')) else 'sds_scan'}/"
                      f"{sta}/{y}/{m:02d}/{f.name}",
            "sampled_at": f"{y}-{m:02d}-01",  # representative date
            "authority": "authoritative",
            "authority_overrides": {"sensor": "operator_input"},
            "recorder": hdr.get("recorder", "unknown"),
            "sensor": hdr.get("sensor", "unknown"),
            "sample_rate": hdr.get("sample_rate"),
            "gain": hdr.get("gain", 1),
            "location_seen": hdr.get("location_seen", "00"),
            "channels_seen": hdr.get("channels_seen", []),
            "network_code_seen": hdr.get("network_code_seen", "unknown"),
            "recorder_raw_code": hdr.get("recorder_raw_code"),
            "sensor_raw_code": hdr.get("sensor_raw_code"),
            "boundary_pinned": False,
            "flags": [],
        }
        # Flags
        if hdr.get("recorder") == "unknown" or obs["recorder"] == "unknown":
            obs["flags"].append("catalogue_gap")
        if hdr.get("sensor") == "unknown":
            obs["flags"].append("suspicious_unknown_sensor")
        flags_aggregate.update(obs["flags"])
        network_codes_seen.add(obs["network_code_seen"])
        locations_seen.add(obs["location_seen"])

        raw_obs.append(obs)

    print(f"  raw observations: {len(raw_obs)} (skipped {skipped})", file=sys.stderr)

    # v3c: Gecko .ss sidecar observations (chronologically dedup'd)
    ss_obs = scan_gecko_ss(archive_root, sta)
    if ss_obs:
        print(f"  gecko_ss observations: {len(ss_obs)} (dedup'd from settings_time stream)",
              file=sys.stderr)

    # Dedup the monthly raw_obs into the main observations list
    deduped = dedup_observations(raw_obs)
    print(f"  dedup'd observations: {len(deduped)}", file=sys.stderr)

    # Day-level bisection of each detected transition. Pins the FIRST_SEEN
    # of the next observation to day-level. Does NOT modify the previous
    # observation's last_seen (which stays at the actual last sampled
    # date) — bisection tells us when the new tuple appeared, not when
    # the old tuple stopped. For gap cases (OUTU 2001-11 → 2014-12 = 13
    # year gap), obs_lo's last_seen stays at 2001-11-01; obs_hi's
    # first_seen gets bisection-pinned.
    GAP_THRESHOLD_DAYS = 180  # >6 months = treat as data gap, end unpinned
    reg_recs = reg.get("recorder_types") or []
    reg_singleton = (reg_recs[0] if isinstance(reg_recs, list)
                     and len(reg_recs) == 1 else None)
    for i in range(len(deduped) - 1):
        obs_lo = deduped[i]
        obs_hi = deduped[i + 1]
        last_actual_lo = _dt.date.fromisoformat(obs_lo["last_seen"])
        first_actual_hi = _dt.date.fromisoformat(obs_hi["first_seen"])
        gap_days = (first_actual_hi - last_actual_lo).days
        t_lo = value_tuple(obs_lo)
        t_hi = value_tuple(obs_hi)

        if gap_days > GAP_THRESHOLD_DAYS:
            # Data gap, not a transition. Don't bisect (would be
            # misleading). Mark obs_lo's end as unpinned-due-to-gap and
            # leave obs_hi's first_seen at actual first sampled date.
            print(f"    gap {last_actual_lo} ↔ {first_actual_hi} "
                  f"({gap_days}d) — no bisect, flag obs_lo as gap_end",
                  file=sys.stderr)
            obs_lo["_gap_after"] = True
            continue

        pinned = bisect_transition(
            archive_root, sta, last_actual_lo, first_actual_hi, t_lo, t_hi,
            disk_to_sds, reg_singleton,
        )
        print(f"    bisect {last_actual_lo} ↔ {first_actual_hi}: pinned at {pinned}",
              file=sys.stderr)
        obs_hi["first_seen"] = pinned.isoformat()
        obs_hi["boundary_pinned"] = True
        # obs_lo gets day-before-pinned as last_seen only if NOT a gap case
        obs_lo["last_seen"] = (pinned - _dt.timedelta(days=1)).isoformat()
        obs_lo["boundary_pinned"] = True

    # Sparse-observation flag — when a tuple's coverage is suspiciously
    # thin (e.g. 2 samples spread across years), the synthesis side
    # should know not to trust the epoch span at face value.
    for obs in deduped:
        first = _dt.date.fromisoformat(obs["first_seen"])
        last = _dt.date.fromisoformat(obs["last_seen"])
        span_months = max(1, (last - first).days // 30)
        if obs["sample_count"] / span_months < 0.3 and span_months > 6:
            obs.setdefault("flags", []).append("sparse_observation")

    proposed = derive_proposed_epochs(deduped)
    print(f"  proposed epochs: {len(proposed)}", file=sys.stderr)

    # Note: we deliberately do NOT emit a `network_code_drift` flag on
    # epochs even when multiple network codes were seen. In the VW archive,
    # codes like UM/AB/ABC are parse-noise on a bogus pre-2025-VW-formal-
    # registration network field; the existing VW conversion already maps
    # all of these to VW per the registry. Flagging drift across them is
    # over-reading. The raw codes stay in `archive_network_codes_seen`
    # (audit trail at the station level) but don't gate epoch identity.

    # Combine monthly-derived observations + .ss-derived observations
    # into a single observations[] list, with source_type field
    # distinguishing them. Sort by sampled_at for readability.
    all_observations = deduped + ss_obs
    all_observations.sort(key=lambda o: (o["sampled_at"], o.get("source_type", "")))
    # Aggregate network codes + locations from .ss too
    for ss in ss_obs:
        network_codes_seen.add(ss.get("network_code_seen", "unknown"))
        locations_seen.add(ss.get("location_seen", "00"))

    return {
        "archive_first_data": raw_obs[0]["sampled_at"] if raw_obs else None,
        "archive_last_data": raw_obs[-1]["sampled_at"] if raw_obs else None,
        "archive_network_codes_seen": sorted(network_codes_seen),
        "location_codes_seen": sorted(locations_seen),
        "observations": all_observations,
        "proposed_epochs": proposed,
        "gecko_ss_observations_count": len(ss_obs),
    }


# === YAML emission =========================================================

def get_git_sha(repo_root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root), capture_output=True, text=True, check=True,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def emit_yaml(out_path: Path, network: str, stations: dict, archive_root: Path,
              scan_strategy: dict, args) -> None:
    doc = OrderedDict()
    doc["network"] = network
    doc["schema_version"] = 1
    doc["generated_at"] = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
    doc["generator"] = "eqserver_2_seiscomp/scan/level2_metadata_scan.py"
    doc["generator_version"] = get_git_sha(REPO_ROOT)
    doc["source_archive"] = str(archive_root)
    doc["scan_strategy"] = scan_strategy

    populated = [(sta, info) for sta, info in stations.items()
                 if not info.get("_no_data")]
    empty = [sta for sta, info in stations.items() if info.get("_no_data")]

    total_obs = sum(len(info["observations"]) for sta, info in populated)
    doc["scan_summary"] = {
        "stations_scanned": len(stations),
        "stations_with_data": len(populated),
        "unique_observations": total_obs,
    }
    doc["stations"] = OrderedDict()
    for sta, info in populated:
        doc["stations"][sta] = info
    if empty:
        doc["stations_with_no_data"] = empty

    # Recursively convert OrderedDict → dict; YAML SafeDumper rejects
    # OrderedDict by default. We also drop internal _-prefixed keys
    # (audit-trail stuff like _raw_codes that isn't in the schema).
    def _clean(o):
        if isinstance(o, OrderedDict):
            o = dict(o)
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items() if not k.startswith("_")}
        if isinstance(o, list):
            return [_clean(x) for x in o]
        return o

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write(f"# Generated by {doc['generator']} — DO NOT EDIT BY HAND.\n")
        f.write(f"# Edit by rerunning the sweep.\n\n")
        yaml.safe_dump(_clean(doc), f, sort_keys=False, default_flow_style=False, width=120)
    print(f"\n[scan] wrote {out_path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--network", default="VW")
    ap.add_argument("--stations", required=True,
                    help="comma-separated, e.g. TRPU,OUTU")
    ap.add_argument("--plans-dir", type=Path, required=True)
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--archive-root", type=Path, required=True)
    ap.add_argument("--disk-to-sds", type=Path, default=DISK_TO_SDS_DEFAULT)
    ap.add_argument("--out", type=Path, required=True,
                    help="output YAML path")
    args = ap.parse_args()

    for p, desc in [
        (args.plans_dir, "plans dir"),
        (args.registry, "registry"),
        (args.archive_root, "archive root"),
        (args.disk_to_sds, "disk_to_sds engine path"),
    ]:
        if not p.exists():
            sys.exit(f"ERROR: {desc} not found: {p}")

    stations = args.stations.split(",")
    print(f"[scan] host: {socket.gethostname()}", file=sys.stderr)
    print(f"[scan] network={args.network}  stations={stations}", file=sys.stderr)

    results = OrderedDict()
    for sta in stations:
        results[sta] = scan_station(
            sta, args.archive_root, args.plans_dir,
            args.registry, args.disk_to_sds,
        )

    scan_strategy = {
        "version": "v3-monthly-bisect-ss",
        "sampling": "one file per (station, year-month), first day of month",
        "boundary_bracket": False,
        "bisection": True,
        "bisection_resolution": "day",
        "ss_sidecar_reader": True,
        "note": ("v3: monthly survey + day-level bisection + Gecko .ss "
                 "sidecar reader (dedup'd by config-tuple, pinned to "
                 "settings_time). v3a: gap-aware dedup. v3b extension-"
                 "aware picker deferred — not needed for calibration set."),
    }
    emit_yaml(args.out, args.network, results, args.archive_root,
              scan_strategy, args)


if __name__ == "__main__":
    main()

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


def pick_representative_file(archive_root: Path, sta: str,
                             year: int, month: int) -> Optional[Path]:
    """Pick one file from the first day of the month that has any data.

    Prefers .dmx/.dmx.gz (PC-SUDS — full metadata); falls back to .ms.zip
    or .ms (MiniSEED — sample_rate only).
    """
    m_dir = archive_root / sta / "continuous" / str(year) / f"{month:02d}"
    if not m_dir.exists():
        return None
    for d_dir in sorted(m_dir.iterdir()):
        if not d_dir.is_dir():
            continue
        # First try PC-SUDS
        for ext in [".dmx", ".dmx.gz"]:
            for f in sorted(d_dir.iterdir()):
                if not f.is_file():
                    continue
                if f.name.endswith(ext) and ".trig" not in f.name:
                    return f
        # Then mseed
        for ext in [".ms.zip", ".ms"]:
            for f in sorted(d_dir.iterdir()):
                if not f.is_file():
                    continue
                if f.name.endswith(ext) and "_CHZ" not in f.name and "_CHN" not in f.name and "_CHE" not in f.name:
                    return f
        # If nothing matched, give up on this day and move to next
    return None


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
        out["gain"] = int(sb.get("atod_gain") or 1) or 1
        out["max_gain"] = float(sb.get("max_gain") or 0.0)
        out["con_mvolts"] = float(sb.get("con_mvolts") or 0.0)
        out["st_lat"] = float(sb.get("st_lat") or 0.0)
        out["st_long"] = float(sb.get("st_long") or 0.0)
        out["elev"] = float(sb.get("elev") or 0.0)
        if stationcomp.get("statident"):
            si = stationcomp["statident"]
            out["network_code_seen"] = (si.get("network") or "").strip("\x00 ") or "unknown"
    return out


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

    Each dedup'd observation becomes one epoch. Boundary-pin policy:
    - First epoch's `start` is `archive_first_data` (the first sampled_at)
    - Last epoch's `end` is `archive_last_data` if station is closed in
      our scope, else `open`
    - Internal transitions are `pinned_at_<YYYY-MM-DD>` to month-level
      precision (no bisection in v1)
    """
    epochs = []
    for i, obs in enumerate(deduped):
        is_first = i == 0
        is_last = i == len(deduped) - 1
        ep = {
            "start": obs["first_seen"],
            "end": None if is_last else deduped[i+1]["first_seen"],
            "recorder": obs.get("recorder", "unknown"),
            "sensor": obs.get("sensor", "unknown"),
            "sample_rate": obs.get("sample_rate"),
            "gain": obs.get("gain", 1),
            "location": obs.get("location_seen", "00"),
            "confidence": "medium",     # month-level pinning, no bisection
            "boundary_pin": {
                "start": "archive_first_data" if is_first else f"pinned_at_{obs['first_seen']}",
                "end": ("open" if is_last
                        else f"pinned_at_{deduped[i+1]['first_seen']}"),
            },
            "observations": 1,
            "flags": [],
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

    # Dedup and derive
    deduped = dedup_observations(raw_obs)
    proposed = derive_proposed_epochs(deduped)
    print(f"  dedup'd observations: {len(deduped)}", file=sys.stderr)
    print(f"  proposed epochs: {len(proposed)}", file=sys.stderr)

    # Network code drift flag
    if len(network_codes_seen) > 1:
        for ep in proposed:
            ep["flags"].append("network_code_drift")

    return {
        "archive_first_data": raw_obs[0]["sampled_at"] if raw_obs else None,
        "archive_last_data": raw_obs[-1]["sampled_at"] if raw_obs else None,
        "archive_network_codes_seen": sorted(network_codes_seen),
        "location_codes_seen": sorted(locations_seen),
        "observations": deduped,
        "proposed_epochs": proposed,
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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        f.write(f"# Generated by {doc['generator']} — DO NOT EDIT BY HAND.\n")
        f.write(f"# Edit by rerunning the sweep.\n\n")
        yaml.safe_dump(dict(doc), f, sort_keys=False, default_flow_style=False, width=120)
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
        "version": "v1-monthly",
        "sampling": "one file per (station, year-month), first day of month",
        "boundary_bracket": False,
        "bisection": False,
        "ss_sidecar_reader": False,
        "note": "v1 calibration scope; no bisection yet — boundaries pinned to month level only",
    }
    emit_yaml(args.out, args.network, results, args.archive_root,
              scan_strategy, args)


if __name__ == "__main__":
    main()

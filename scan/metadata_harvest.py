#!/usr/bin/env python3
"""Phase 2a metadata harvest.

Walks the Level-1 manifest to harvest per-station metadata observations from:
  - EchoPro PC-SUDS .dmx headers (STATIONCOMP + DESCRIPTRACE blocks; via sudspy)
  - Gecko .ss kelunjimeta sidecars (plain text key=value pairs)

For EchoPro, samples one representative file per (station, year) — the SUDS
embeds full metadata in every file, so per-year sampling is enough to detect
epoch boundaries (recorder/sensor/sample_rate changes). Bisection refinement
to month/day/file granularity can be layered later if observations show change
between adjacent samples.

For Gecko, reads ALL .ss files for the station and deduplicates by content
fingerprint — each distinct settings_time configuration becomes one observation.

Emits per-station YAML at <out>/<NET>.<STA>.observations.yaml that matches
the uom_seismic_metadata observations schema (see CLAUDE.md "Station metadata
epochs" section). Source-tagged per observation: `pcsuds/<file>` or
`gecko_ss/<file>`. Each observation carries `authority` and per-field
`authority_overrides` (operator-input vs authoritative).

Run:
  python3 scan/metadata_harvest.py <manifest.db> --registry metadata/station_registry.yaml --out metadata/observations/
"""
from __future__ import annotations
import argparse
import os
import re
import sqlite3
import sys
import traceback
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "sudspy"))  # for local dev import
# On the VM, sudspy is installed in the disk_to_sds venv — adjust SUDSPY_PATH or
# run inside that venv if importing fails.

# ---- Constants ----
# EchoPro STATIONCOMP recorder/sensor codes are single chars and largely opaque
# without the SUDS reference; we record the raw char and let curation map it
# (kept as `recorder_code` / `sensor_code` in the observation, not interpreted).
ECHOPRO_AUTHORITATIVE_FIELDS = {
    "recorder_code", "sample_rate", "max_gain", "con_mvolts",
    "lat", "lon", "elev", "datalogger_channel", "atod_gain",
}
ECHOPRO_OPERATOR_INPUT_FIELDS = {
    "sensor_code", "data_units", "polarity", "sitecondition", "enclosure",
}

# Gecko .ss kelunjimeta — line format: key=value (with quotes around some values).
# Authority is per-field: serial/cpv/firmware/sample_rate are recorder-authored;
# sensor_name/sens/sitename/network_code are operator-input.
GECKO_AUTHORITATIVE_FIELDS = {
    "serial", "cpv", "current_gain", "sampling_rate",
    "firmware_version", "build", "settings_time",
}
GECKO_OPERATOR_INPUT_FIELDS = {
    "sensor_name", "sens", "sitename", "network_code", "location_id",
}


# ---- Gecko .ss parser ----
SS_LINE = re.compile(r'^([^=]+)=(.*)$')


def parse_kelunjimeta(text):
    """Parse a Gecko .ss kelunjimeta sidecar.

    Returns a dict of key→value (strings, with surrounding quotes stripped).
    Tolerant of older firmware quirks (e.g. trailing junk on a `gain` line).
    """
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = SS_LINE.match(line)
        if not m:
            continue
        k = m.group(1).strip().strip('"').strip()
        v = m.group(2).strip()
        # strip a trailing inline `"foo"=...` artifact seen on older firmware
        if '"' in v and "=" in v:
            v = v.split('"', 1)[0].strip()
        v = v.strip('"').strip()
        out[k] = v
    return out


def gecko_fingerprint(parsed):
    """Content fingerprint for deduping .ss snapshots within a station.
    Same fingerprint = same configuration; we keep one observation per
    distinct config (the FIRST settings_time in which it appeared, plus
    a count of how many .ss snapshots shared it)."""
    return (
        parsed.get("serial", ""),
        parsed.get("cpv", ""),
        parsed.get("current_gain", ""),
        parsed.get("sampling_rate", ""),
        parsed.get("firmware_version", ""),
        parsed.get("network_code", ""),
        parsed.get("location_id", ""),
        parsed.get("sensor_name", ""),
        parsed.get("sens", ""),
    )


def harvest_gecko_station(conn, station):
    """All distinct .ss configurations for one station, ordered by first-seen time.

    Index-only on (station, role); we sort by settings_time in Python after
    parse, so the SQL has no ORDER BY (otherwise sqlite spill-sorts thousands
    of .ss rows per station).
    """
    rows = list(conn.execute(
        "SELECT path FROM files WHERE station = ? AND role = 'metadata'",
        (station,),
    ))
    seen = {}  # fingerprint -> {first_path, count, settings_time, parsed}
    parse_errors = 0
    for (path,) in rows:
        try:
            with open(path, "r", errors="replace") as f:
                txt = f.read()
            parsed = parse_kelunjimeta(txt)
        except OSError:
            parse_errors += 1
            continue
        fp = gecko_fingerprint(parsed)
        if fp in seen:
            seen[fp]["count"] += 1
        else:
            seen[fp] = {
                "parsed": parsed,
                "first_path": path,
                "count": 1,
                "settings_time": parsed.get("settings_time", ""),
            }
    observations = []
    items = sorted(seen.values(), key=lambda r: r.get("settings_time") or "")
    for rec in items:
        p = rec["parsed"]
        obs = {
            "source": f"gecko_ss/{os.path.basename(rec['first_path'])}",
            "source_path": rec["first_path"],
            "authority": "authoritative",
            "authority_overrides": {k: "operator_input" for k in GECKO_OPERATOR_INPUT_FIELDS if k in p},
            "settings_time": p.get("settings_time"),
            "snapshot_count": rec["count"],
            "fields": {
                # authoritative
                "serial": p.get("serial"),
                "cpv": p.get("cpv"),
                "current_gain": p.get("current_gain"),
                "sampling_rate": p.get("sampling_rate"),
                "firmware_version": p.get("firmware_version") or p.get("firmware"),
                "build": p.get("build"),
                # operator-input
                "sensor_name": p.get("sensor_name"),
                "sens": p.get("sens"),
                "sitename": p.get("sitename"),
                "network_code": p.get("network_code"),
                "location_id": p.get("location_id"),
                # per-channel gain
                "gain_A_0": p.get("gain_A_0"),
                "gain_A_1": p.get("gain_A_1"),
                "gain_A_2": p.get("gain_A_2"),
                # storing/tele channel mapping
                **{k: p.get(k) for k in p if k.startswith("storing_chan") or k.startswith("tele_chan")},
            },
        }
        observations.append(obs)
    return observations, parse_errors


# ---- EchoPro .dmx harvest (sudspy) ----
def harvest_echopro_file(path):
    """Open a single .dmx, extract STATIONCOMP+DESCRIPTRACE bodies.

    Returns the per-file observation dict, or None on parse failure.
    Requires sudspy on PYTHONPATH.
    """
    from sudspy.io import iter_suds_blocks
    from sudspy.parsers import parse_stationcomp_struct, parse_descriptrace_struct

    stationcomp = None
    descriptrace = None
    try:
        for block in iter_suds_blocks(path, skip_data=True, strict=False):
            if block.struct_type == 5 and stationcomp is None:
                try:
                    stationcomp = parse_stationcomp_struct(block)
                except Exception:
                    pass
            elif block.struct_type == 7 and descriptrace is None:
                try:
                    descriptrace = parse_descriptrace_struct(block)
                except Exception:
                    pass
            if stationcomp and descriptrace:
                break
    except Exception:
        return None

    if not stationcomp or not descriptrace:
        return None

    sc = stationcomp["struct_body"]
    dt = descriptrace["struct_body"]
    longident = stationcomp.get("longident") or {}
    statident = stationcomp.get("statident") or {}

    return {
        "source": f"pcsuds/{os.path.basename(path)}",
        "source_path": path,
        "authority": "authoritative",
        "authority_overrides": {k: "operator_input" for k in ECHOPRO_OPERATOR_INPUT_FIELDS},
        "fields": {
            "network_in_file": (longident.get("network") or statident.get("network") or "").strip(),
            "station_in_file": (longident.get("station") or statident.get("station") or "").strip(),
            "component_in_file": (longident.get("component") or statident.get("component") or "").strip(),
            "sample_rate": dt.get("rate"),
            "recorder_code": sc.get("recorder"),
            "sensor_code": sc.get("sensor_type"),
            "data_units": sc.get("data_units"),
            "polarity": sc.get("polarity"),
            "max_gain": sc.get("max_gain"),
            "con_mvolts": sc.get("con_mvolts"),
            "clip_value": sc.get("clip_value"),
            "atod_gain": sc.get("atod_gain"),
            "datalogger_channel": sc.get("channel"),
            "lat": sc.get("st_lat"),
            "lon": sc.get("st_long"),
            "elev": sc.get("elev"),
            "enclosure": sc.get("enclosure"),
            "sitecondition": sc.get("sitecondition"),
            "azim": sc.get("azim"),
            "incid": sc.get("incid"),
            "effective": sc.get("effective"),
        },
    }


def harvest_echopro_station(conn, station):
    """One representative .dmx per (station, year), parsed via sudspy.

    Issued as two index-friendly queries (years list, then one LIMIT-1 per year)
    rather than a single GROUP BY MIN(path) — the aggregate forced sqlite to
    sort-spill to disk on 400k-row station-month manifests.
    """
    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT dir_year FROM files WHERE station = ? AND dir_year > 0 "
        "ORDER BY dir_year",
        (station,),
    )]
    observations = []
    parse_errors = []
    for year in years:
        row = conn.execute(
            "SELECT path FROM files WHERE station = ? AND dir_year = ? "
            "  AND recorder_type = 'echopro' AND source_type = 'disk' "
            "  AND exclude_reason IS NULL LIMIT 1",
            (station, year),
        ).fetchone()
        if row is None:
            continue
        path = row[0]
        obs = harvest_echopro_file(path)
        if obs is None:
            parse_errors.append((year, path))
            continue
        obs["sample_year"] = year
        observations.append(obs)
    return observations, parse_errors


# ---- Per-station driver ----
def epochify(observations, key_fields):
    """Group consecutive observations sharing the same fingerprint into epochs.
    `key_fields` = list of dotted-path keys into obs['fields'] that define
    epoch identity (recorder, sensor_code, sample_rate)."""
    def fp(obs):
        f = obs.get("fields", {})
        return tuple(f.get(k) for k in key_fields)
    epochs = []
    cur = None
    for obs in observations:
        sig = fp(obs)
        if cur is None or cur["fingerprint"] != sig:
            if cur is not None:
                epochs.append(cur)
            cur = {"fingerprint": sig, "observations": []}
        cur["observations"].append(obs)
    if cur is not None:
        epochs.append(cur)
    return epochs


def harvest_station(conn, station, registry_entry):
    """Returns (echopro_observations, echopro_errors, gecko_observations, gecko_errors)."""
    rt = (registry_entry or {}).get("recorder_types") or []
    do_echopro = "echopro" in rt or not rt  # default on if registry unannotated
    do_gecko = "gecko" in rt or not rt
    eobs, eerrs = (harvest_echopro_station(conn, station) if do_echopro else ([], []))
    gobs, gerrs = (harvest_gecko_station(conn, station) if do_gecko else ([], 0))
    return eobs, eerrs, gobs, gerrs


def build_station_observations_yaml(station, registry_entry, eobs, gobs):
    net = (registry_entry or {}).get("target_network")
    epochs_echo = epochify(eobs, ["recorder_code", "sensor_code", "sample_rate"])
    return {
        "station": station,
        "network": net,
        "registry_recorder_types": (registry_entry or {}).get("recorder_types"),
        "echopro": {
            "n_samples": len(eobs),
            "n_epochs": len(epochs_echo),
            "epochs": [
                {
                    "fingerprint": {
                        "recorder_code": ep["fingerprint"][0],
                        "sensor_code": ep["fingerprint"][1],
                        "sample_rate": ep["fingerprint"][2],
                    },
                    "years": [o.get("sample_year") for o in ep["observations"]],
                    "observations": ep["observations"],
                }
                for ep in epochs_echo
            ],
        },
        "gecko": {
            "n_distinct_configs": len(gobs),
            "observations": gobs,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", help="Level-1 manifest SQLite path")
    ap.add_argument("--registry", required=True,
                    help="path to metadata/station_registry.yaml")
    ap.add_argument("--out", default="metadata/observations",
                    help="output directory for <NET>.<STA>.observations.yaml")
    ap.add_argument("--stations", default="",
                    help="comma-separated subset (default: all include:true with data)")
    ap.add_argument("--skip-echopro", action="store_true")
    ap.add_argument("--skip-gecko", action="store_true")
    args = ap.parse_args()

    import yaml
    registry = {s: v for s, v in yaml.safe_load(open(args.registry)).items()
                if isinstance(v, dict) and v.get("include") is True}

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station_dir ON files(station, dir_year, dir_month, dir_day)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_station_role ON files(station, role)")
    conn.commit()

    present = {r[0] for r in conn.execute("SELECT DISTINCT station FROM files")}
    if args.stations:
        target = [s.strip() for s in args.stations.split(",") if s.strip()]
    else:
        target = sorted(present & set(registry.keys()))

    os.makedirs(args.out, exist_ok=True)
    print(f"[meta-harvest] {len(target)} stations to harvest from {args.db}")

    for sta in target:
        reg_entry = registry.get(sta)
        try:
            eobs, eerrs, gobs, gerrs = harvest_station(conn, sta, reg_entry)
        except Exception:
            print(f"  {sta:6} HARVEST EXCEPTION")
            traceback.print_exc()
            continue
        if not eobs and not gobs:
            print(f"  {sta:6} no echopro/gecko data, skipping")
            continue
        doc = build_station_observations_yaml(sta, reg_entry, eobs, gobs)
        net = doc["network"] or "UNK"
        path = os.path.join(args.out, f"{net}.{sta}.observations.yaml")
        with open(path, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False)
        print(f"  {net}.{sta:6} echo_samples={len(eobs):>3} echo_epochs={doc['echopro']['n_epochs']:>2} "
              f"gecko_configs={len(gobs):>3} errs_echo={len(eerrs):>3}")

    print(f"\n[meta-harvest] wrote observations YAMLs to {args.out}/")


if __name__ == "__main__":
    sys.exit(main())

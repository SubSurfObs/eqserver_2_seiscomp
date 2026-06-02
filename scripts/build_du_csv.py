"""Build du_stations.csv from all available sources for the standalone DU
station-registry repo.

Sources merged (priority for channel info: VIP > FDSN > LT > SAA > inferred):
  - Registry        eqserver_2_seiscomp/metadata/station_registry.yaml
  - VIP             /tmp/vip_status.json
  - FDSN snapshot   eqserver_2_seiscomp/metadata/uploaded/DU/du.xml
  - LT archive      /mnt/seiscomp_archive/<YEAR>/DU/<STA>/
  - SAA xlsx        eqserver_2_seiscomp/metadata/uploaded/DU/SAA_stations DanS Apr 2026.xlsx
  - GoingToEqs xlsx eqserver_2_seiscomp/metadata/uploaded/DU/GoingToEqserver2025.xlsx
  - Op DL/EW/KM     eqserver_2_seiscomp/metadata/derived/DU stns 27May2026_{DL,EW}.xlsx
  - Op ListToCheck  eqserver_2_seiscomp/metadata/derived/ListToCheck27May2026.xlsx

Scope: include = true OR unknown; target_network = DU OR (null AND DU-relevant
per operator data). Excludes VW, VX, and include:false.
"""
from __future__ import annotations
import csv
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone

import openpyxl
import yaml

REPO = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp"
LT_ROOT = "/mnt/seiscomp_archive"
DU_DIR = f"{REPO}/metadata/uploaded/DU"
DERIVED = f"{REPO}/metadata/derived"
VIP_JSON = "/tmp/vip_status.json"

# Columns (order matters; this is the CSV header):
COLUMNS = [
    "tier",                 # 1=have channel high-conf, 2=have channel low-conf, 3=needs_input
    "station",              # SEED station code
    "include",              # true | unknown
    "place",                # human-readable site name
    "state",                # AU state code
    "recorder",             # echopro | gecko | piesmo | minimus | reftek_rt130 | unknown
    "sample_rate_hz",       # int
    "sensor_class",         # broadband | short_period | accelerometer | unknown
    "location",             # SEED location code (00, 60, AB, ...)
    "band",                 # 2-char stem (HH, EH, SH, CH, FH) — implies Z/N/E
    "convention",           # seed | gecko | explicit
    "eqserver_first_year",  # earliest year of data in EqServer source archive (DU cutoff 2017)
    "eqserver_last_year",   # latest year of data in EqServer source archive
    "seiscomp_first_year",        # earliest year of data in SeisComP LT archive
    "seiscomp_last_year",         # latest year of data in LT (or "live" if VIP active)
    "vip_status",           # active | inactive | not_in_vip
    "operator_status",      # o | blank  (c excluded — closed stations filtered out)
    "channel_source",       # vip | fdsn | lt | saa | inferred | needs_input
    "notes",                # combined freeform
]

# Tier mapping (drives the on-disk row order — tier ASC, station ASC):
TIER_BY_SOURCE = {
    "vip": 1, "fdsn": 1, "seiscomp": 1,  # have channel, high confidence
    "saa": 2, "inferred": 2,              # have channel, lower confidence
    "needs_input": 3,                     # no channel info anywhere
}

# DU-specific cutoff for presenting eqserver first-year. The DU network was
# brought across to EqServer around 2017 (operator-confirmed 2026-06-02);
# any earlier year directories on EqServer for DU stations are pre-GPS-lock
# WNRO artifacts (the same class that phase3's _filter_bogus_year_traces
# guard drops at 2012 at conversion time). Apply 2017 here so the CSV shows
# operator-meaningful coverage; phase3's filter is separate and unchanged.
DU_EQSERVER_MIN_YEAR = 2017


def load_registry():
    with open(f"{REPO}/metadata/station_registry.yaml") as f:
        return yaml.safe_load(f)


def fetch_vip():
    with open(VIP_JSON) as f:
        d = json.load(f)
    out = {}
    for s in d["stations"]:
        out[(s["network"], s["station"])] = s
    return out


def parse_fdsn():
    ns = {"s": "http://www.fdsn.org/xml/station/1"}
    tree = ET.parse(f"{DU_DIR}/du.xml")
    root = tree.getroot()
    out = defaultdict(list)
    for net in root.findall(".//s:Network", ns):
        for sta in net.findall("s:Station", ns):
            code = sta.get("code")
            for ch in sta.findall("s:Channel", ns):
                loc = ch.get("locationCode", "") or ""
                chan = ch.get("code")
                sr_el = ch.find("s:SampleRate", ns)
                rate = float(sr_el.text) if sr_el is not None else None
                out[code].append((loc, chan, rate))
    return dict(out)


def scan_lt():
    """Return {station: {(loc, chan, year)}} for DU stations in LT."""
    out = defaultdict(set)
    for yr_dir in sorted(glob.glob(f"{LT_ROOT}/[12][0-9][0-9][0-9]/DU"), reverse=True):
        yr = int(os.path.basename(os.path.dirname(yr_dir)))
        for sta_dir in glob.glob(f"{yr_dir}/*"):
            sta = os.path.basename(sta_dir)
            for chan_d in glob.glob(f"{sta_dir}/*.D"):
                chan = os.path.basename(chan_d)[:-2]
                files = glob.glob(f"{chan_d}/*")
                if files:
                    parts = os.path.basename(files[0]).split(".")
                    loc = parts[2] if len(parts) >= 4 else ""
                    out[sta].add((loc, chan, yr))
    return dict(out)


def scan_eqserver_years(stn):
    """Direct ls of /mnt/eqserver_archive/.../<STN>/continuous/<YEAR>/.
    Returns a set of years. Empty if station has no EqServer dir."""
    base = f"/mnt/eqserver_archive/shared/data/repository/archive/{stn}/continuous"
    if not os.path.isdir(base):
        return set()
    return {int(d) for d in os.listdir(base) if d.isdigit() and 1980 < int(d) < 2100}


def parse_saa_dans():
    """SAA DanS Apr 2026 (and SAA_stations.xlsx) — has channel codes + coords."""
    out = {}
    for fname in ["SAA_stations DanS Apr 2026.xlsx", "SAA_stations.xlsx"]:
        path = f"{DU_DIR}/{fname}"
        if not os.path.exists(path):
            continue
        wb = openpyxl.load_workbook(path, data_only=True)
        for sh in wb.sheetnames:
            ws = wb[sh]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            header = [str(c).strip() if c else "" for c in rows[0]]
            try:
                idx = {h: header.index(h) for h in header if h}
            except ValueError:
                continue
            for r in rows[1:]:
                if not r or not r[0]:
                    continue
                cells = [str(c).strip() if c is not None else "" for c in r]
                if len(cells) < len(header):
                    cells += [""] * (len(header) - len(cells))
                stn = cells[idx.get("station code", 2)] if "station code" in idx else ""
                if not stn:
                    continue
                # Keep first seen
                if stn in out:
                    continue
                out[stn] = {
                    "net": cells[idx["network code"]] if "network code" in idx else "",
                    "loc": cells[idx["location code"]] if "location code" in idx else "",
                    "chan": cells[idx["channel code"]] if "channel code" in idx else "",
                    "lat": cells[idx["latitude"]] if "latitude" in idx else "",
                    "lon": cells[idx["longitude"]] if "longitude" in idx else "",
                    "elev": cells[idx["elevation"]] if "elevation" in idx else "",
                    "city": cells[idx["Location"]] if "Location" in idx else "",
                    "site": cells[idx["site name"]] if "site name" in idx else "",
                    "src_file": fname,
                }
    return out


def parse_going_to_eq():
    """GoingToEqserver2025: Stn | State | Method | Recorder | Hardware | Note."""
    out = {}
    path = f"{DU_DIR}/GoingToEqserver2025.xlsx"
    if not os.path.exists(path):
        return out
    wb = openpyxl.load_workbook(path, data_only=True)
    for sh in wb.sheetnames:
        ws = wb[sh]
        rows = list(ws.iter_rows(values_only=True))
        header = None
        for r in rows:
            cells = [str(c).strip() if c else "" for c in r]
            if "Stn" in cells:
                header = cells
                continue
            if header is None:
                continue
            if not cells[0]:
                continue
            stn = cells[0]
            def field(name):
                try:
                    return cells[header.index(name)]
                except (ValueError, IndexError):
                    return ""
            out[stn] = {
                "state": field("State"),
                "method": field("Method"),
                "recorder": field("Recorder"),
                "hardware": field("Hardware"),
                "note": field("Note"),
            }
    return out


def parse_operator_responses():
    """Combine DL/EW DU-stns + ListToCheck operator inputs into a single dict
    per station with the most informative non-empty values."""
    out = defaultdict(lambda: {"persons": set(), "places": set(), "statuses": set(), "notes": []})
    files = [
        f"{DERIVED}/DU stns 27May2026_DL.xlsx",
        f"{DERIVED}/DU stns 27May2026_EW.xlsx",
        f"{DERIVED}/ListToCheck27May2026.xlsx",
    ]
    for path in files:
        if not os.path.exists(path):
            continue
        bn = os.path.basename(path)
        wb = openpyxl.load_workbook(path, data_only=True)
        for sh in wb.sheetnames:
            ws = wb[sh]
            header = None
            for r in ws.iter_rows(values_only=True):
                cells = [str(c).strip() if c is not None else "" for c in r]
                if cells and cells[0] == "STN":
                    header = cells
                    continue
                if header is None or not cells or not cells[0] or cells[0].startswith("#"):
                    continue
                stn = cells[0]
                person = cells[1] if len(cells) > 1 else ""
                if len(header) == 6:
                    place = cells[3] or cells[2] if len(cells) > 3 else ""
                    status = cells[4] if len(cells) > 4 else ""
                    note = cells[5] if len(cells) > 5 else ""
                else:
                    place = ""
                    status = cells[2] if len(cells) > 2 else ""
                    note = cells[3] if len(cells) > 3 else ""
                if person: out[stn]["persons"].add(person)
                if place: out[stn]["places"].add(place)
                if status: out[stn]["statuses"].add(status.lower())
                if note: out[stn]["notes"].append(f"{bn.split()[0]}:{note}")
    return out


SHORT_PERIOD_BANDS = {"E", "S"}
BROADBAND_BANDS = {"H", "C", "B", "F"}


def derive_sensor_class(band):
    """band is a 2-char stem like 'EH' or 'HH'. First char = band code."""
    if not band or len(band) < 1:
        return ""
    first = band[0]
    if first in SHORT_PERIOD_BANDS:
        return "short_period"
    if first in BROADBAND_BANDS:
        return "broadband"
    return ""


def collapse_channels_to_stems(channels):
    """[(loc, chan), ...] -> {loc: set of band-stems}. Drop orientation suffix."""
    out = defaultdict(set)
    for loc, chan in channels:
        if len(chan) >= 2:
            out[loc].add(chan[:2])
    return dict(out)


def pick_primary_loc_band(loc_bands):
    """From {loc: {stems}}, pick the most likely PRIMARY entry.
    Prefer locations that look like real seismometer locs ('00', '60') over
    accelerometer locs ('AB'). Within a loc, prefer broadband over accel."""
    if not loc_bands:
        return "", ""
    # Score locations
    def loc_score(loc):
        if loc in ("00", "0"): return 3
        if loc == "60": return 2
        if loc == "AB": return 0
        return 1
    locs_sorted = sorted(loc_bands.keys(), key=lambda l: -loc_score(l))
    primary_loc = locs_sorted[0]
    bands = loc_bands[primary_loc]
    # Prefer non-accelerometer bands (H over N as 2nd char)
    def band_score(b):
        if len(b) < 2: return 0
        # accelerometer 2nd char N => lower priority
        if b[1] == "N": return 0
        return 1
    primary_band = sorted(bands, key=lambda b: -band_score(b))[0]
    return primary_loc, primary_band


def format_multi_locband(loc_bands):
    """Return 'loc.band; loc.band' for stations with multi-loc/band entries."""
    parts = []
    for loc in sorted(loc_bands.keys()):
        for band in sorted(loc_bands[loc]):
            parts.append(f"{loc}.{band}")
    return "; ".join(parts)


def main():
    reg = load_registry()
    vip = fetch_vip()
    fdsn = parse_fdsn()
    lt = scan_lt()
    saa = parse_saa_dans()
    gte = parse_going_to_eq()
    ops = parse_operator_responses()

    # Filter stations in scope
    scope = []
    for stn, body in reg.items():
        if not isinstance(body, dict):
            continue
        inc = body.get("include")
        net = body.get("target_network")
        if inc is False:
            continue
        # Include criteria:
        if net == "DU":
            scope.append(stn)
            continue
        # include:unknown with DU-related operator input
        if (inc in (None, "unknown")) and stn in ops:
            scope.append(stn)
    scope = sorted(set(scope))
    print(f"Stations in scope: {len(scope)}", file=sys.stderr)

    # Build rows
    rows = []
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for stn in scope:
        body = reg.get(stn, {}) or {}
        # Channel: prefer VIP > FDSN > LT > SAA
        vip_entry = vip.get(("DU", stn))
        loc = band = ""
        sample_rate = ""
        channel_source = "needs_input"
        # VIP
        if vip_entry:
            ch_list = vip_entry["channels"]  # ['00.HHZ', ...]
            parsed = []
            for c in ch_list:
                if "." in c:
                    l, ch = c.split(".", 1)
                    parsed.append((l, ch))
            lb = collapse_channels_to_stems(parsed)
            if len(lb) > 1 or (lb and len(next(iter(lb.values()))) > 1):
                loc_band_str = format_multi_locband(lb)
                # Multi: still put primary in loc/band, full in notes
                primary_loc, primary_band = pick_primary_loc_band(lb)
                loc = primary_loc
                band = primary_band
            else:
                loc, band = pick_primary_loc_band(lb)
            channel_source = "vip"
        # FDSN fallback
        if not band and stn in fdsn:
            parsed = [(l, c) for l, c, _r in fdsn[stn]]
            lb = collapse_channels_to_stems(parsed)
            loc, band = pick_primary_loc_band(lb)
            channel_source = "fdsn"
            # also extract rate
            rates = [r for _l, _c, r in fdsn[stn] if r]
            if rates:
                sample_rate = str(int(rates[0]))
        # SeisComP LT fallback
        if not band and stn in lt:
            parsed = [(l, c) for l, c, _y in lt[stn]]
            lb = collapse_channels_to_stems(parsed)
            loc, band = pick_primary_loc_band(lb)
            channel_source = "seiscomp"
        # SAA fallback
        if not band and stn in saa:
            sa = saa[stn]
            if sa["chan"]:
                loc = sa["loc"]
                band = sa["chan"][:2]
                channel_source = "saa"

        # Sample rate refinement from VIP/FDSN (FDSN already done; VIP doesn't carry rate directly)
        # Try to infer from FDSN match even if VIP gave band
        if not sample_rate and stn in fdsn:
            rates = [r for _l, _c, r in fdsn[stn] if r]
            if rates:
                sample_rate = str(int(rates[0]))

        # Recorder
        rec_types = body.get("recorder_types") or []
        recorder = rec_types[0] if rec_types else ""
        if not recorder and stn in gte:
            gt_rec = gte[stn]["recorder"].lower()
            if "epro" in gt_rec or "echopro" in gt_rec:
                recorder = "echopro"
            elif "gecko" in gt_rec:
                recorder = "gecko"
            elif "piesmo" in gt_rec or "peismo" in gt_rec:
                recorder = "piesmo"
            elif "minimus" in gt_rec or "radian" in gt_rec:
                recorder = "minimus"
        if not recorder:
            recorder = "unknown"

        # State (from GoingToEqs)
        state = gte.get(stn, {}).get("state", "")
        # Place (prefer operator, then SAA city, then GoingToEqs)
        place = ""
        if stn in ops and ops[stn]["places"]:
            place = sorted(ops[stn]["places"])[0]
        elif stn in saa:
            place = saa[stn]["city"] or saa[stn]["site"].split(",")[0]
        elif stn in gte:
            place = ""

        # Sensor class
        sensor_class = derive_sensor_class(band)

        # EqServer first/last year (DU cutoff applied to drop pre-2017 artifacts)
        eq_years = scan_eqserver_years(stn)
        eq_years_filtered = {y for y in eq_years if y >= DU_EQSERVER_MIN_YEAR}
        if eq_years_filtered:
            eqserver_first_year = str(min(eq_years_filtered))
            eqserver_last_year = str(max(eq_years_filtered))
        else:
            eqserver_first_year = ""
            eqserver_last_year = ""

        # LT first/last year (no cutoff — LT only post-dates the migration)
        seiscomp_first_year = seiscomp_last_year = ""
        if stn in lt:
            lt_yrs = {y for _l, _c, y in lt[stn]}
            if lt_yrs:
                seiscomp_first_year = str(min(lt_yrs))
                seiscomp_last_year = str(max(lt_yrs))

        # VIP status
        if vip_entry:
            vip_status = "active" if vip_entry["active"] else "inactive"
            if vip_entry["active"]:
                seiscomp_last_year = "live"
        else:
            vip_status = "not_in_vip"

        # Operator status (DL 2026-06-02: 'c' means physically shut down,
        # those rows are filtered out at the registry level; this column
        # now only carries 'o' for stations the operator confirmed operational).
        op_st = ""
        if stn in ops:
            sts = ops[stn]["statuses"]
            if "o" in sts:
                op_st = "o"

        # Convention
        # Default: 'seed' for DU (full SEED naming with corner-period dep)
        # 'gecko' for stations where Gecko-by-rate is the rule (none of our DU
        # use this by default but operators can override)
        convention = "seed"

        # Notes — combine sources
        note_parts = []
        if stn in ops:
            persons = sorted(ops[stn]["persons"])
            if persons:
                note_parts.append(f"op[{','.join(persons)}]")
            for n in ops[stn]["notes"]:
                note_parts.append(n)
        if stn in gte and gte[stn]["note"]:
            note_parts.append(f"GoingToEq:{gte[stn]['note']}")
        if stn in gte and gte[stn]["method"]:
            note_parts.append(f"comms:{gte[stn]['method']}")
        if stn in gte and gte[stn]["hardware"]:
            note_parts.append(f"hw:{gte[stn]['hardware']}")
        # Registry notes (truncated)
        if body.get("notes"):
            reg_notes = body["notes"].replace("\n", " ").strip()
            if len(reg_notes) > 80:
                reg_notes = reg_notes[:77] + "..."
            note_parts.append(f"reg:{reg_notes}")
        # Multi-band/loc note
        if vip_entry:
            parsed = []
            for c in vip_entry["channels"]:
                if "." in c:
                    parsed.append(c.split(".", 1))
            lb = collapse_channels_to_stems(parsed)
            full = format_multi_locband(lb)
            primary = f"{loc}.{band}" if loc and band else ""
            if full and full != primary:
                note_parts.append(f"vip_all:{full}")
        notes = " | ".join(note_parts)

        inc_val = body.get("include")
        if inc_val is True:
            include_str = "true"
        elif inc_val is False:
            include_str = "false"
        else:
            include_str = "unknown"

        tier = TIER_BY_SOURCE.get(channel_source, 3)

        rows.append({
            "tier": tier,
            "station": stn,
            "include": include_str,
            "place": place,
            "state": state,
            "recorder": recorder,
            "sample_rate_hz": sample_rate,
            "sensor_class": sensor_class,
            "location": loc,
            "band": band,
            "convention": convention,
            "eqserver_first_year": eqserver_first_year,
            "eqserver_last_year": eqserver_last_year,
            "seiscomp_first_year": seiscomp_first_year,
            "seiscomp_last_year": seiscomp_last_year,
            "vip_status": vip_status,
            "operator_status": op_st,
            "channel_source": channel_source,
            "notes": notes,
        })

    # Sort: tier ASC (most info first), then station ASC within each tier.
    rows.sort(key=lambda r: (r["tier"], r["station"]))

    # Write CSV
    out_path = "/tmp/du_stations.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {out_path}: {len(rows)} rows.", file=sys.stderr)
    by_tier = defaultdict(int)
    for r in rows:
        by_tier[r["tier"]] += 1
    for t in sorted(by_tier):
        print(f"  tier {t}: {by_tier[t]} rows", file=sys.stderr)


if __name__ == "__main__":
    main()

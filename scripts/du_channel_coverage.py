"""DU channel-code coverage v2 — VIP + FDSN + LT + spreadsheet-with-channels.

Headline question: of the 53 DU include:true stations, how many have NO
channel-code information ANYWHERE — not in VIP, not in FDSN snapshot, not
in LT archive, and no spreadsheet row nominates a channel code?
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
import openpyxl
import yaml

REPO = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp"
LT_ROOT = "/mnt/seiscomp_archive"
DU_DIR = f"{REPO}/metadata/uploaded/DU"
VIP_JSON = "/tmp/vip_status.json"

CHAN_RE = re.compile(r"\b([BEHCFS][HNDLJYQ][ZNE])\b")


def load_registry_du():
    with open(f"{REPO}/metadata/station_registry.yaml") as f:
        reg = yaml.safe_load(f)
    return sorted(
        k for k, v in reg.items()
        if isinstance(v, dict)
        and v.get("include") is True
        and v.get("target_network") == "DU"
    )


def vip_du(stations):
    with open(VIP_JSON) as f:
        d = json.load(f)
    out = {}
    for s in d["stations"]:
        if s["network"] == "DU" and s["station"] in stations:
            out[s["station"]] = {
                "channels": s["channels"],
                "active": s["active"],
                "in_vip": s["in_vip"],
            }
    return out


def fdsn_du(stations):
    ns = {"s": "http://www.fdsn.org/xml/station/1"}
    tree = ET.parse(f"{DU_DIR}/du.xml")
    root = tree.getroot()
    out = defaultdict(list)
    for net in root.findall(".//s:Network", ns):
        for sta in net.findall("s:Station", ns):
            code = sta.get("code")
            if code not in stations:
                continue
            for ch in sta.findall("s:Channel", ns):
                loc = ch.get("locationCode", "") or ""
                chan = ch.get("code")
                out[code].append(f"{loc}.{chan}")
    return {s: out[s] for s in stations if s in out}


def lt_du(stations):
    out = defaultdict(set)
    for yr_dir in sorted(glob.glob(f"{LT_ROOT}/[12][0-9][0-9][0-9]/DU"), reverse=True):
        yr = os.path.basename(os.path.dirname(yr_dir))
        for sta_dir in glob.glob(f"{yr_dir}/*"):
            sta = os.path.basename(sta_dir)
            if sta not in stations:
                continue
            for chan_d in glob.glob(f"{sta_dir}/*.D"):
                chan = os.path.basename(chan_d)[:-2]
                files = glob.glob(f"{chan_d}/*")
                if files:
                    parts = os.path.basename(files[0]).split(".")
                    loc = parts[2] if len(parts) >= 4 else ""
                    out[sta].add((loc, chan, int(yr)))
    return {s: out[s] for s in stations if s in out}


def spreadsheet_channels(stations):
    """For each station, scan every xlsx in metadata/uploaded/DU/ and look
    for rows containing the station code. If any cell in that row matches
    a SEED channel-code pattern (e.g. HHZ, EHZ, SHZ), record it.

    Returns {station: [{file, sheet, channels_found, row_text}, ...]}.
    """
    out = defaultdict(list)
    for xlsx in sorted(glob.glob(f"{DU_DIR}/*.xlsx")):
        bn = os.path.basename(xlsx)
        if bn.startswith("~$"):
            continue
        try:
            wb = openpyxl.load_workbook(xlsx, data_only=True)
        except Exception as e:
            print(f"  warn: {bn}: {e}", file=sys.stderr)
            continue
        for sh in wb.sheetnames:
            ws = wb[sh]
            for row in ws.iter_rows(values_only=True):
                if not row:
                    continue
                cells = [str(c) if c is not None else "" for c in row]
                joined = " | ".join(cells)
                # Identify which station(s) this row mentions
                matched_stations = [s for s in stations if s in cells]
                if not matched_stations:
                    continue
                # Look for channel-like codes anywhere in the row
                chans = set(CHAN_RE.findall(joined))
                # Filter out false-positive 3-letter words that look like channels
                # (rare; e.g. "EHE" is a real channel; "WHN"/etc. would be filtered)
                valid_chans = {c for c in chans if c[0] in "BEHCFS" and c[1] in "HNDLJYQ" and c[2] in "ZNE"}
                if valid_chans:
                    for s in matched_stations:
                        out[s].append({
                            "file": bn,
                            "sheet": sh,
                            "channels": sorted(valid_chans),
                            "row_summary": joined[:120],
                        })
    return out


def main():
    stations = load_registry_du()
    print(f"Analyzing {len(stations)} DU include:true stations.\n")

    vip = vip_du(stations)
    fdsn = fdsn_du(stations)
    lt = lt_du(stations)
    ssh = spreadsheet_channels(stations)

    print(f"VIP coverage:                 {len(vip)} / {len(stations)}")
    print(f"FDSN snapshot coverage:       {len(fdsn)} / {len(stations)}")
    print(f"LT archive coverage:          {len(lt)} / {len(stations)}")
    print(f"Spreadsheet w/ channel info:  {len(ssh)} / {len(stations)}")
    print()

    # Per-station summary
    zero_info = []
    print(f"{'STA':<8} {'VIP':<24} {'FDSN snap':<22} {'LT (chans seen)':<28} {'xlsx w/ chans':<30}")
    print("-" * 120)
    for sta in stations:
        vip_str = ",".join(vip[sta]["channels"]) if sta in vip else "—"
        fdsn_str = ",".join(fdsn[sta]) if sta in fdsn else "—"
        if sta in lt:
            chans_seen = sorted({f"{loc}/{c}" for loc, c, _yr in lt[sta]})
            lt_str = ",".join(chans_seen)
        else:
            lt_str = "—"
        if sta in ssh:
            ssh_str = ",".join(sorted({c for entry in ssh[sta] for c in entry["channels"]}))
        else:
            ssh_str = "—"
        has_any = (sta in vip) or (sta in fdsn) or (sta in lt) or (sta in ssh)
        flag = "" if has_any else " <-- ZERO INFO"
        if not has_any:
            zero_info.append(sta)
        # Truncate long strings for layout
        print(f"{sta:<8} {vip_str[:23]:<24} {fdsn_str[:21]:<22} {lt_str[:27]:<28} {ssh_str[:29]:<30}{flag}")
    print("-" * 120)
    print()
    print(f"ZERO-INFO STATIONS (no channel code in ANY source): {len(zero_info)} / {len(stations)}")
    if zero_info:
        for s in zero_info:
            print(f"  - {s}")


if __name__ == "__main__":
    main()

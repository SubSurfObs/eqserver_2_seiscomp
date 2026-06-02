"""DU channel-code coverage analysis.

For each DU station with include:true in the registry, check which of the
available sources has channel/location info:
  S1a — FDSN snapshot (metadata/uploaded/DU/du.xml)
  S1b — LT SeisComP archive (/mnt/seiscomp_archive/<YEAR>/DU/<STA>/)
  S2  — SAA operator spreadsheets in metadata/uploaded/DU/*.xlsx
  S3  — upstream EchoPro->seedlink mapping file (location TBD)

Output: a single-table coverage matrix, one row per station, one column
per source, with the channel codes found (or "—" if absent).
"""
from __future__ import annotations
import glob
import os
import re
import sys
import yaml
from collections import defaultdict
from pathlib import Path

REPO = "/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp"
LT_ROOT = "/mnt/seiscomp_archive"
DU_DIR = f"{REPO}/metadata/uploaded/DU"


def load_registry_du_include():
    with open(f"{REPO}/metadata/station_registry.yaml") as f:
        reg = yaml.safe_load(f)
    return sorted(
        k for k, v in reg.items()
        if isinstance(v, dict)
        and v.get("include") is True
        and v.get("target_network") == "DU"
    )


def s1a_fdsn(stations):
    """Parse du.xml; return {sta: [(loc, chan, rate), ...]}."""
    import xml.etree.ElementTree as ET
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
                sr = ch.find("s:SampleRate", ns)
                rate = float(sr.text) if sr is not None else None
                out[code].append((loc, chan, rate))
    return {s: out[s] for s in stations}


def s1b_lt_archive(stations):
    """Walk LT archive looking for any DU/<STA>/<CHA>.D/ dirs.
    Returns {sta: {(loc, chan)}}."""
    out = defaultdict(set)
    for yr_dir in sorted(glob.glob(f"{LT_ROOT}/[12][0-9][0-9][0-9]/DU"), reverse=True):
        for sta_dir in glob.glob(f"{yr_dir}/*"):
            sta = os.path.basename(sta_dir)
            if sta not in stations:
                continue
            for chan_d in glob.glob(f"{sta_dir}/*.D"):
                chan = os.path.basename(chan_d)[:-2]
                # Sample a file to extract location code
                files = glob.glob(f"{chan_d}/*")
                if files:
                    fn = os.path.basename(files[0])
                    parts = fn.split(".")
                    # Expected: NET.STA.LOC.CHAN.D.YEAR.JDAY
                    loc = parts[2] if len(parts) >= 4 else ""
                    out[sta].add((loc, chan))
    return {s: out[s] for s in stations}


def s2_saa_spreadsheets(stations):
    """Read all .xlsx in metadata/uploaded/DU/. For each, find the column
    most likely to hold a station code and record any rows matching our
    station list, capturing all cells from that row."""
    import openpyxl
    out = defaultdict(lambda: defaultdict(list))  # sta -> filename -> [rows]
    for xlsx in sorted(glob.glob(f"{DU_DIR}/*.xlsx")):
        bn = os.path.basename(xlsx)
        if bn.startswith("~$"):
            continue
        try:
            wb = openpyxl.load_workbook(xlsx, data_only=True)
        except Exception as e:
            print(f"  warn: could not open {bn}: {e}", file=sys.stderr)
            continue
        for sh in wb.sheetnames:
            ws = wb[sh]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            # Build a set of all cell values per row
            for r in rows:
                if not r:
                    continue
                cells = [str(c).strip() if c is not None else "" for c in r]
                for c in cells:
                    if c in stations:
                        out[c][bn].append({
                            "sheet": sh,
                            "row": cells,
                        })
                        break
    return out


def format_matrix(stations, s1a, s1b, s2):
    print(f"{'STA':<8} {'S1a (FDSN)':<28} {'S1b (LT archive)':<24} {'S2 (SAA xlsx)':<35}")
    print("-" * 100)
    n_s1a = n_s1b = n_s2 = n_any = 0
    for sta in stations:
        empty_loc = '""'
        s1a_str = ", ".join(
            "{}/{}@{}".format(loc or empty_loc, chan, int(rate) if rate else "?")
            for loc, chan, rate in (s1a.get(sta) or [])
        ) or "—"
        s1b_str = ", ".join(
            "{}/{}".format(loc or empty_loc, chan)
            for loc, chan in sorted(s1b.get(sta) or [])
        ) or "—"
        s2_str = ", ".join(s2.get(sta, {}).keys()) or "—"
        has_s1a = s1a_str != "—"
        has_s1b = s1b_str != "—"
        has_s2 = s2_str != "—"
        if has_s1a:
            n_s1a += 1
        if has_s1b:
            n_s1b += 1
        if has_s2:
            n_s2 += 1
        if has_s1a or has_s1b or has_s2:
            n_any += 1
        print(f"{sta:<8} {s1a_str:<28} {s1b_str:<24} {s2_str:<35}")
    print("-" * 100)
    print(f"\nCoverage out of {len(stations)} DU include:true stations:")
    print(f"  S1a (FDSN snapshot):    {n_s1a:>3}/{len(stations)}")
    print(f"  S1b (LT archive):       {n_s1b:>3}/{len(stations)}")
    print(f"  S2  (SAA spreadsheets): {n_s2:>3}/{len(stations)}")
    print(f"  ANY source:             {n_any:>3}/{len(stations)}")
    print(f"  NO source (BLOCKED):    {len(stations)-n_any:>3}/{len(stations)}")


def main():
    stations = load_registry_du_include()
    print(f"Analyzing {len(stations)} DU include:true stations...\n")
    s1a = s1a_fdsn(stations)
    s1b = s1b_lt_archive(stations)
    s2 = s2_saa_spreadsheets(stations)
    format_matrix(stations, s1a, s1b, s2)


if __name__ == "__main__":
    main()

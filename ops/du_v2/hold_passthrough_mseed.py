#!/usr/bin/env python3
"""Codex 179cd8d condition: on release 2cb091c the per-file adapter rewrote
miniSEED channel codes by sample rate (`_enforce_band_instrument`) even for
plans whose names are header passthrough. Hold EVERY converted DU v2
station-year that (a) has a plan without server_verbatim naming and (b)
contains miniSEED waveform files, until the full channel census clears it or
a fixed rebuild replaces it. Idempotent; run on a loop (--loop) so years that
finish later are held too. Nothing held here may be promoted."""
import datetime
import glob
import json
import os
import sqlite3
import sys
import time

import yaml

H = os.path.expanduser("~")
PLANS = f"{H}/projects/SubSurfObs_releases/du_v2/eqserver_2_seiscomp/plans/DU"
REASON = ("Codex 179cd8d: converted by 2cb091c, whose miniSEED adapter rewrote "
          "header channel codes by rate despite passthrough naming — held until "
          "the channel census clears it or a fixed rebuild replaces it")
_mseed_years = {}


def mseed_years(sta):
    if sta not in _mseed_years:
        db = f"{H}/station_dbs/DU.{sta}.db"
        ys = set()
        if os.path.exists(db):
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            ys = {y for (y,) in c.execute(
                "select distinct dir_year from files where station=? and role='waveform' "
                "and (path like '%.ms.zip' or path like '%.ms' or path like '%.ms.gz' "
                "or path like '%.mseed%')", (sta,))}
        _mseed_years[sta] = ys
    return _mseed_years[sta]


def passthrough_stations():
    out = set()
    for pf in glob.glob(f"{PLANS}/DU.*.plan.yaml"):
        p = yaml.safe_load(open(pf))
        if p.get("naming") != "server_verbatim":
            out.add(p["station"])
    return out


def one_pass():
    pt = passthrough_stations()
    n = 0
    for q in sorted(glob.glob("/mnt/seiscomp_staging/eqserver_sweep_du_v2*_s*")):
        try:
            done = [json.loads(l) for l in open(f"{q}/convert_done.jsonl")]
        except FileNotFoundError:
            continue
        try:
            held = {json.loads(l)["run_id"] for l in open(f"{q}/held.jsonl")}
        except FileNotFoundError:
            held = set()
        with open(f"{q}/held.jsonl", "a") as h:
            for r in done:
                if (r["sta"] in pt and int(r["year"]) in mseed_years(r["sta"])
                        and r["run_id"] not in held):
                    h.write(json.dumps({"run_id": r["run_id"], "net": "DU", "sta": r["sta"],
                        "year": r["year"], "action": "held", "reason": REASON,
                        "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}) + "\n")
                    held.add(r["run_id"])
                    n += 1
                    print(f"held {r['sta']} {r['year']}", flush=True)
    return n


if __name__ == "__main__":
    if "--loop" in sys.argv:
        while True:
            one_pass()
            time.sleep(600)
    print(f"{one_pass()} newly held")

#!/usr/bin/env python3
"""Apply Dan's ruling (2026-11-02) to corrupt-source days: a station-year
whose ONLY non-ok days are qc_flagged solely because a source file fails its
own gzip checksum ("incorrect data check") counts as done; those files'
samples are excluded and each is noted. Anything else stays failed.
Dry run by default; --commit appends the done records.
Usage: accept_crc_failures.py [--commit]"""
import datetime
import glob
import json
import sys

CRC = "incorrect data check"
commit = "--commit" in sys.argv
for q in sorted(glob.glob("/mnt/seiscomp_staging/eqserver_sweep_du_v2*_s*")):
    def recs(n):
        try:
            return [json.loads(l) for l in open(f"{q}/{n}")]
        except FileNotFoundError:
            return []
    done = {(r["sta"], int(r["year"])) for r in recs("convert_done.jsonl")}
    latest = {}
    for r in recs("convert_failed.jsonl"):
        latest[(r["sta"], int(r["year"]))] = r
    for key, r in sorted(latest.items()):
        if key in done:
            continue
        mpath = r.get("run_manifest_path") or f"{q}/run_manifests/{r['run_id']}.json"
        try:
            m = json.load(open(mpath))
        except Exception:
            continue
        bad = [d for d in m["eqserver"]["per_date_status"]
               if d.get("status") not in ("ok", "no_files")]
        eq = m["eqserver"]
        if (not bad or eq.get("partial_completion")
                or any(d.get("status") != "qc_flagged"
                       # EVERY read error of the day must be a CRC failure
                       or str(d.get("error", "")).count(CRC) != d.get("read_errors")
                       for d in bad)):
            continue
        files = sorted({e.split("', ")[0].strip("[('")
                        for d in bad for e in [str(d.get("error", ""))]})
        note = (f"accepted per Dan 2026-11-02: {len(bad)} day(s) with a source "
                f"file failing its own gzip CRC; those files' samples excluded: "
                + "; ".join(f.rsplit("/", 1)[-1] for f in files))
        print(f"{'ACCEPT' if commit else 'would accept'}  {key[0]} {key[1]}  {note}")
        if commit:
            with open(f"{q}/convert_done.jsonl", "a") as f:
                f.write(json.dumps({"action": "done", "net": "DU", "sta": key[0],
                    "year": key[1], "run_id": r["run_id"],
                    "run_manifest_path": mpath, "note": note,
                    "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}) + "\n")

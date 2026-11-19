#!/usr/bin/env python3
"""Accept-by-ruling done records for v2.8 (rebuild_s8) and v2.9 (rebuild_s9)
station-years whose only failed days are already-ruled classes. Any other
cause aborts. Dry run unless --commit."""
import collections
import datetime
import json
import re
import sys

COMMIT = "--commit" in sys.argv
ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
PLAN = {"rebuild_s8": ["BRAD:2016", "BRAD:2017", "BRAD:2019", "WALM:2014",
                       "WALM:2015", "WALM:2021", "PEGU:2021", "RNDA2:2025"],
        "rebuild_s9": ["BRSA:2018", "BRSA:2019", "NSTM:2025", "WEPH:2025",
                       "PLY3:2026"]}
NET = {"BRAD": "VW", "WALM": "VW", "PEGU": "VW"}
CLASSES = [
    (("Error -3", "incorrect data check"),
     "damaged compressed file (Dan 2026-11-10: skip unreadable)", True),
    (("zero output",),
     "day has no files of its own, only the previous day's tail (nothing to convert)", False),
    (("record-scan unparseable",),
     "record-scan fallback only, as accepted per Codex 182b49e/b370d14", True),
    (("identity collision",),
     "sample-rate change inside one file, overlapping rates (Dan >99% ruling)", True),
]
for q, units in PLAN.items():
    d = "/mnt/seiscomp_staging/eqserver_sweep_du_v2_" + q
    done = {(r["sta"], int(r["year"])) for r in map(json.loads, open(d + "/convert_done.jsonl"))}
    fails = list(map(json.loads, open(d + "/convert_failed.jsonl")))
    out = []
    for u in units:
        sta, y = u.split(":")
        y = int(y)
        if (sta, y) in done:
            print("already done", q, u)
            continue
        r = [x for x in fails if x["sta"] == sta and int(x["year"]) == y][-1]
        mp = d + "/run_manifests/" + r["run_id"] + ".json"
        days = json.load(open(mp))["eqserver"]["per_date_status"]
        parts = collections.defaultdict(list)
        for x in days:
            if x["status"] in ("ok", "no_files"):
                continue
            e = str(x.get("error", ""))
            for keys, label, with_files in CLASSES:
                if any(k in e for k in keys):
                    if with_files:
                        files = sorted({p.rsplit("/", 1)[-1] for p in re.findall(r"\('(/[^']+)'", e)})
                        parts[label] += [x["date"] + " " + f for f in files] or [x["date"]]
                    else:
                        parts[label].append(x["date"])
                    break
            else:
                sys.exit(f"UNCLASSIFIED {q} {u} {x['date']} {e[:150]}")
        note = "accepted per Dan's rulings: " + "; ".join(
            f"{k}: {', '.join(v)}" for k, v in parts.items())
        out.append({"action": "done", "net": NET.get(sta, "DU"), "sta": sta, "year": y,
                    "run_id": r["run_id"], "run_manifest_path": mp,
                    "staging_root": "/mnt/seiscomp_staging/seiscomp_archive",
                    "note": note, "ts": ts})
        print(("ACCEPT " if COMMIT else "would accept ") + q, u, note[:180])
    if COMMIT and out:
        with open(d + "/convert_done.jsonl", "a") as f:
            for rec in out:
                f.write(json.dumps(rec) + "\n")

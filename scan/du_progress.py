#!/usr/bin/env python3
"""du_progress — terminal progress report for the DU release-v1 conversion.

One line per station: its batch, a mark per year, and a done count.
  ✔ converted (every day ok or no files)   ✘ failed/held   ▶ converting now
  · waiting

Read-only. Run on the staging VM:  python3 ~/du_progress.py
"""
import glob
import json
import os
import re
import subprocess

import yaml

HOME = os.path.expanduser("~")
REL = f"{HOME}/projects/SubSurfObs_releases/du_v1/eqserver_2_seiscomp"
QUEUES = "/mnt/seiscomp_staging/eqserver_sweep_du_v1_s{}"
STATUS = f"{HOME}/v3_prod/du_v1.status"
LOGS = HOME + "/v3_prod/logs_du_v1/convert.b{}.log"

G, R, Y, D, B, X = ("\033[32m", "\033[31m", "\033[33m", "\033[2m",
                    "\033[1m", "\033[0m")


def batches():
    """{batch: [stations]} from the launch lines in the status log."""
    out = {}
    for line in open(STATUS):
        m = re.match(r"\[v1 b(\d+)\] \S+ start: (.*)", line.strip())
        if m:
            out[int(m.group(1))] = m.group(2).split(",")
    return out


def years(sta):
    p = yaml.safe_load(open(f"{REL}/plans/DU/DU.{sta}.plan.yaml"))
    return sorted({int(e["start"][:4]) for e in p["epochs"]
                   if int(e["start"][:4]) >= 2012})


def records(k, name):
    try:
        return [json.loads(l) for l in open(QUEUES.format(k) + "/" + name)]
    except FileNotFoundError:
        return []


def running(k):
    """(station, year) the batch is converting now, from its log tail."""
    try:
        lines = open(LOGS.format(k)).read().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        m = re.match(r"\[convert\] \[\d+/\d+\] (\S+) (\d{4}) \.\.\.$", line)
        if m:
            return m.group(1), int(m.group(2))
        if re.match(r"\[convert\] \[\d+/\d+\] (OK|FAIL)", line):
            return None
    return None


def paused():
    try:
        out = subprocess.run(["ps", "-eo", "stat,args"], capture_output=True,
                             text=True).stdout
    except Exception:
        return False
    return any(l.startswith("T") and "run_production_convert" in l
               for l in out.splitlines())


def main():
    bs = batches()
    tot_done = tot_all = st_done = 0
    rows = []
    for k in sorted(bs):
        done = {(r["sta"], int(r["year"])) for r in records(k, "convert_done.jsonl")}
        bad = {(r["sta"], int(r["year"])) for r in records(k, "convert_failed.jsonl")}
        bad |= {(r["sta"], int(r["year"])) for r in records(k, "held.jsonl")}
        now = running(k)
        for sta in bs[k]:
            ys = years(sta)
            marks, n = [], 0
            for y in ys:
                if (sta, y) in bad:
                    marks.append(f"{R}✘{X}")
                elif (sta, y) in done:
                    marks.append(f"{G}✔{X}")
                    n += 1
                elif now == (sta, y):
                    marks.append(f"{Y}▶{X}")
                else:
                    marks.append(f"{D}·{X}")
            tot_done += n
            tot_all += len(ys)
            complete = n == len(ys) and ys
            st_done += bool(complete)
            flag = f"{G}✔ done{X}" if complete else (
                f"{Y}converting{X}" if now and now[0] == sta else "")
            span = f"{ys[0]}–{ys[-1]}" if ys else "—"
            rows.append((sta, k, span, marks, n, len(ys), flag))
    width = 40
    fill = int(width * tot_done / max(tot_all, 1))
    print(f"\n{B}DU conversion — release v1{X}\n")
    wmax = max((len(r[3]) for r in rows), default=0)
    for sta, k, span, marks, n, ny, flag in rows:
        pad = "  " * (wmax - len(marks))
        print(f"  {sta:<6} b{k}  {span:<9}  {' '.join(marks)}{pad}"
              f"   {n:>2}/{ny:<2} {flag}")
    print(f"\n  {G}{'█' * fill}{X}{D}{'░' * (width - fill)}{X}  "
          f"{tot_done}/{tot_all} station-years  ·  {st_done}/{len(rows)} stations complete")
    print(f"  {G}✔{X} converted  {R}✘{X} failed/held  {Y}▶{X} converting  {D}·{X} waiting"
          + (f"   {Y}(paused by disk guard){X}" if paused() else "") + "\n")


if __name__ == "__main__":
    main()

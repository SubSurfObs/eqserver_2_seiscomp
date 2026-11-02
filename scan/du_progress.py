#!/usr/bin/env python3
"""du_progress — terminal progress report for the DU release-v1 conversion.

One line per station: its batch, a mark per year, and a done count.
  ✔ converted AND passed Codex's independent sample check
  ✓ converted (every day ok or no files), not yet sampled by Codex
  ! Codex alert (needs a look)   ✘ failed/held   ▶ converting now   · waiting

Read-only. Run on the staging VM:  python3 ~/du_progress.py [du_v2|du_v1]
"""
import glob
import json
import os
import re
import subprocess

import sys

import yaml

HOME = os.path.expanduser("~")
RELEASE = sys.argv[1] if len(sys.argv) > 1 else "du_v2"   # e.g. du_v1
TAG = RELEASE.split("_")[-1]                               # v1, v2
REL = f"{HOME}/projects/SubSurfObs_releases/{RELEASE}/eqserver_2_seiscomp"
QUEUES = "/mnt/seiscomp_staging/eqserver_sweep_" + RELEASE + "_s{}"
STATUS = f"{HOME}/v3_prod/{RELEASE}.status"
LOGS = HOME + f"/v3_prod/logs_{RELEASE}/convert.b" + "{}.log"

G, R, Y, D, B, X = ("\033[32m", "\033[31m", "\033[33m", "\033[2m",
                    "\033[1m", "\033[0m")


def batches():
    """{batch: [stations]} from the launch lines in the status log."""
    out = {}
    for line in open(STATUS):
        m = re.match(r"\[" + TAG + r" b(\d+)\] \S+ start: (.*)", line.strip())
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


def codex_qc():
    """{(sta, year): verdict} — latest verdict per station-year from Codex's
    read-only live QC watcher (~/codex_sandbox/live_qc/results.jsonl)."""
    out = {}
    try:
        for l in open(f"{HOME}/codex_sandbox/live_qc/results.jsonl"):
            r = json.loads(l)
            out[(r["sta"], int(r["year"]))] = r.get("verdict")
    except (FileNotFoundError, ValueError):
        pass
    return out


def guard_pause():
    """Minutes the disk guard has been pausing file-list builds (None if
    not paused) — from the last guard line in any release status log."""
    import datetime
    last = None
    for f in glob.glob(f"{HOME}/v3_prod/du_v*.status"):
        for line in open(f):
            if line.startswith("[guard]"):
                ts = line.split()[1]
                if last is None or ts > last[0]:
                    last = (ts, "PAUSED" in line)
    if not last or not last[1]:
        return None
    t = datetime.datetime.strptime(last[0], "%Y-%m-%dT%H:%M:%SZ")
    return (datetime.datetime.utcnow() - t).total_seconds() / 60


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
    qc = codex_qc()
    # Targeted rebuilds (e.g. the relay-file fix) land in <release>_rebuild
    # queues; a rebuilt year counts as done even though its original is held.
    rebuilt = set()
    for rq in glob.glob(QUEUES.format("*").replace("_s*", "_rebuild_s*")):
        try:
            rebuilt |= {(json.loads(l)["sta"], int(json.loads(l)["year"]))
                        for l in open(rq + "/convert_done.jsonl")}
        except FileNotFoundError:
            pass
    tot_done = tot_all = st_done = n_qc = n_alert = 0
    rows = []
    for k in sorted(bs):
        done = {(r["sta"], int(r["year"])) for r in records(k, "convert_done.jsonl")}
        bad = {(r["sta"], int(r["year"])) for r in records(k, "convert_failed.jsonl")}
        held = {(r["sta"], int(r["year"])) for r in records(k, "held.jsonl")}
        now = running(k)
        for sta in bs[k]:
            ys = years(sta)
            marks, n = [], 0
            for y in ys:
                if (sta, y) in held and (sta, y) not in rebuilt:   # held, not rebuilt
                    marks.append(f"{R}✘{X}")
                elif (sta, y) in done or (sta, y) in rebuilt:  # retry/rebuild wins
                    v = qc.get((sta, y))
                    if v == "PASS":
                        marks.append(f"{B}{G}✔{X}")
                        n_qc += 1
                    elif v:
                        marks.append(f"\033[35m!{X}")
                        n_alert += 1
                    else:
                        marks.append(f"{G}✓{X}")
                    n += 1
                elif (sta, y) in bad:
                    marks.append(f"{R}✘{X}")
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
    print(f"\n{B}DU conversion — release {TAG}{X}\n")
    gp = guard_pause()
    if gp is not None:
        print(f"  {R}{B}⚠ DISK GUARD: file-list builds PAUSED for {gp/60:.1f} h — "
              f"batches waiting on a file list are stalled{X}\n")
    wmax = max((len(r[3]) for r in rows), default=0)
    for sta, k, span, marks, n, ny, flag in rows:
        pad = "  " * (wmax - len(marks))
        print(f"  {sta:<6} b{k}  {span:<9}  {' '.join(marks)}{pad}"
              f"   {n:>2}/{ny:<2} {flag}")
    print(f"\n  {G}{'█' * fill}{X}{D}{'░' * (width - fill)}{X}  "
          f"{tot_done}/{tot_all} station-years  ·  {st_done}/{len(rows)} stations complete")
    print(f"  Codex independent sample check: {n_qc} station-years PASS"
          + (f", \033[35m{n_alert} alert(s){X}" if n_alert else ", no alerts"))
    print(f"  {B}{G}✔{X} converted + Codex-checked  {G}✓{X} converted  "
          f"{R}✘{X} failed/held  {Y}▶{X} converting  {D}·{X} waiting"
          + (f"   {Y}(paused by disk guard){X}" if paused() else "") + "\n")


if __name__ == "__main__":
    main()

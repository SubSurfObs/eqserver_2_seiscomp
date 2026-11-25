#!/usr/bin/env python3
"""VM disk keeper for the VW/VX re-sweep. Runs on the VM, every 5 minutes:
every file list of a station-year that is DONE or FAILED in any vwvx queue,
and not open by a running process, is gzipped to the share, verified by
sha256, and removed locally. The controls restore a list from the share when
a later year needs it, so nothing is lost. Logs free space each pass and
writes ALARM lines when free space is under the build threshold."""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import time
from glob import glob

SEL = os.path.expanduser("~/v3_prod/selections_vwvx_v3")
ARCH = "/mnt/seiscomp_staging/eqserver_v3_selections/vwvx_v3"
LOG = os.path.expanduser("~/v3_prod/disk_keeper.log")
THRESH_GB = 45


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()) + msg + "\n")


def finished_units():
    out = set()
    for q in glob("/mnt/seiscomp_staging/eqserver_sweep_vwvx_v3_r*"):
        for name in ("convert_done.jsonl", "convert_failed.jsonl"):
            try:
                for line in open(os.path.join(q, name)):
                    r = json.loads(line)
                    out.add((r["sta"], int(r["year"])))
            except FileNotFoundError:
                pass
    return out


def sha(path, opener=open):
    h = hashlib.sha256()
    with opener(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def one_pass():
    done = finished_units()
    open_now = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    freed = n = 0
    for f in sorted(glob(SEL + "/*.selection.sqlite")):
        b = os.path.basename(f)
        net, sta, y = b.split(".")[:3]
        if (sta, int(y)) not in done or b in open_now:
            continue
        g = f"{ARCH}/{net}.{sta}/{b}.gz"
        try:
            os.makedirs(os.path.dirname(g), exist_ok=True)
            if not os.path.exists(g):
                with open(f, "rb") as i, gzip.open(g + ".partial", "wb") as o:
                    shutil.copyfileobj(i, o)
                os.replace(g + ".partial", g)
            if sha(f) == sha(g, gzip.open):
                size = os.path.getsize(f)
                os.remove(f)
                for x in glob(f + ".*.json"):
                    os.remove(x)
                freed += size
                n += 1
            else:
                log(f"MISMATCH {b} (kept locally)")
        except OSError as e:
            log(f"share error on {b}: {e} (kept locally, retry next pass)")
    st = os.statvfs("/")
    free_gb = st.f_bavail * st.f_frsize / 1e9
    log(f"free {free_gb:.0f} GB; archived {n} lists ({freed / 1e9:.1f} GB)")
    if free_gb < THRESH_GB:
        log(f"ALARM free {free_gb:.0f} GB < {THRESH_GB} GB")


if __name__ == "__main__":
    while True:
        try:
            one_pass()
        except Exception as e:  # never die silently
            log(f"keeper error: {type(e).__name__}: {e}")
        time.sleep(300)

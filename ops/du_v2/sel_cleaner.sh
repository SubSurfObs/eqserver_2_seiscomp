#!/bin/bash
# Per-YEAR file-list cleaner (2026-10-31): a station-year's selection is no
# longer needed once that year is recorded done/failed in its queue (years run
# newest-first, so the next-newer year — the only other user, for its Jan-1
# tail — has already run). Archive it (gzip, verified) to the share, then free
# the local copy. Every 3 min; one pass with --once.
set -u
pass() {
python3 - <<'PY'
import glob, gzip, json, os, re, shutil, subprocess
H = os.path.expanduser("~")
# Stations with a live conversion (batch OR retry) keep their file lists:
# a retry restores a failed year's list and needs it for the whole run.
live = set(re.findall(r"--stations (\S+)", subprocess.run(
    ["ps", "-eo", "args"], capture_output=True, text=True).stdout))
sets = [  # (local selections dir, queue glob, archive root)
    (f"{H}/v3_prod/selections_w2", "/mnt/seiscomp_staging/eqserver_sweep_w2_s*",
     "/mnt/seiscomp_staging/eqserver_v3_selections"),
    (f"{H}/v3_prod/selections_du_v1", "/mnt/seiscomp_staging/eqserver_sweep_du_v1_s*",
     "/mnt/seiscomp_staging/eqserver_v3_selections/du_v1"),
    (os.environ.get("CLEAN_SEL", f"{H}/v3_prod/selections_du_v2"),
     os.environ.get("CLEAN_QGLOB", "/mnt/seiscomp_staging/eqserver_sweep_du_v2_s*"),
     os.environ.get("CLEAN_ARCH", "/mnt/seiscomp_staging/eqserver_v3_selections/du_v2")),
]
if os.environ.get("CLEAN_ONLY_V2"):
    sets = sets[-1:]
import fcntl, yaml
_py = {}
def plan_years(sta):
    if sta not in _py:
        try:
            pdir = os.environ.get("CLEAN_PLANS", f"{H}/projects/SubSurfObs_releases/"
                                  "du_v2/eqserver_2_seiscomp/plans/DU")
            p = yaml.safe_load(open(f"{pdir}/DU.{sta}.plan.yaml"))
            _py[sta] = {int(e["start"][:4]) for e in p["epochs"]}
        except Exception:
            _py[sta] = set(range(2000, 2031))   # unknown plan: assume needed
    return _py[sta]
def station_locked(sta):
    path = os.environ.get("LOCKS", f"{H}/v3_prod/locks") + f"/station.{sta}.lock"
    if not os.path.exists(path):
        return False
    with open(path) as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
    return False

freed = 0
for seldir, qglob, arch in sets:
    done, failed = set(), set()
    for q in glob.glob(qglob):
        for name, into in (("convert_done.jsonl", done),
                           ("convert_failed.jsonl", failed)):
            try:
                for l in open(os.path.join(q, name)):
                    r = json.loads(l)
                    into.add((r["sta"], int(r["year"])))
            except FileNotFoundError:
                pass
    for f in glob.glob(f"{seldir}/DU.*.*.selection.sqlite"):
        _, sta, yr = os.path.basename(f).split(".")[:3]
        # A DONE year's list may go once no PENDING year still needs it as
        # the previous year (Jan-1 tail): i.e. year+1 is done or not in the
        # plan. A FAILED year's list goes only when its station lock is free.
        # (Codex 034f35c; lists are restored from the share when needed.)
        key = (sta, int(yr))
        y = int(yr)
        nxt_pending = (y + 1) in plan_years(sta) and (sta, y + 1) not in done
        if key in done:
            if nxt_pending:
                continue
        elif key in failed:
            if station_locked(sta) or sta in live:
                continue
        else:
            continue
        dst_dir = os.path.join(arch, f"DU.{sta}")
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(f) + ".gz")
        tmp = dst + ".partial"
        with open(f, "rb") as src, gzip.open(tmp, "wb") as out:
            shutil.copyfileobj(src, out)
        # verify the archive decompresses to the same size before freeing
        n = 0
        with gzip.open(tmp, "rb") as chk:
            for chunk in iter(lambda: chk.read(1 << 22), b""):
                n += len(chunk)
        if n != os.path.getsize(f):
            os.remove(tmp)
            continue
        os.replace(tmp, dst)
        freed += os.path.getsize(f)
        os.remove(f)
        for side in (f + ".deepok.json",):
            if os.path.exists(side):
                os.remove(side)
if freed:
    with open(f"{H}/v3_prod/du_v1.status", "a") as s:
        import datetime
        s.write(f"[sel-cleaner] {datetime.datetime.utcnow():%Y-%m-%dT%H:%M:%SZ} archived finished-year file lists, freed {freed/1e9:.1f} GB\n")
print(f"freed {freed/1e9:.1f} GB")
PY
}
if [ "${1:-}" = "--once" ]; then pass; exit; fi
while true; do pass > /dev/null 2>&1; sleep 180; done

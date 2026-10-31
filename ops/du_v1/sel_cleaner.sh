#!/bin/bash
# Per-YEAR file-list cleaner (2026-10-31): a station-year's selection is no
# longer needed once that year is recorded done/failed in its queue (years run
# newest-first, so the next-newer year — the only other user, for its Jan-1
# tail — has already run). Archive it (gzip, verified) to the share, then free
# the local copy. Every 3 min; one pass with --once.
set -u
pass() {
python3 - <<'PY'
import glob, gzip, json, os, shutil
H = os.path.expanduser("~")
sets = [  # (local selections dir, queue glob, archive root)
    (f"{H}/v3_prod/selections_w2", "/mnt/seiscomp_staging/eqserver_sweep_w2_s*",
     "/mnt/seiscomp_staging/eqserver_v3_selections"),
    (f"{H}/v3_prod/selections_du_v1", "/mnt/seiscomp_staging/eqserver_sweep_du_v1_s*",
     "/mnt/seiscomp_staging/eqserver_v3_selections/du_v1"),
]
freed = 0
for seldir, qglob, arch in sets:
    finished = set()
    for q in glob.glob(qglob):
        for name in ("convert_done.jsonl", "convert_failed.jsonl"):
            try:
                for l in open(os.path.join(q, name)):
                    r = json.loads(l)
                    finished.add((r["sta"], int(r["year"])))
            except FileNotFoundError:
                pass
    for f in glob.glob(f"{seldir}/DU.*.*.selection.sqlite"):
        _, sta, yr = os.path.basename(f).split(".")[:3]
        if (sta, int(yr)) not in finished:
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

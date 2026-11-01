#!/bin/bash
# DU release v2: retry each failed station-year ONCE (>= 2012), when its
# station is idle — the station lock (ops/du_v2/lib.sh) is taken non-blocking,
# so a retry never overlaps a batch or another retry. convert_year restores or
# rebuilds the year's and previous year's file lists (globally bounded) and
# refuses to convert without the previous year (Jan-1 tail). Every 10 min.
set -u
REL=$HOME/projects/SubSurfObs_releases/du_v2
export REPO=$REL/eqserver_2_seiscomp
export PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
export SHIM=$REL/.pyshim
export SEL=${SEL:-$HOME/v3_prod/selections_du_v2}
export LOG=${LOG:-$HOME/v3_prod/logs_du_v2}
export STATUS=${STATUS:-$HOME/v3_prod/du_v2.status}
export STAGING=${STAGING:-/mnt/seiscomp_staging/seiscomp_archive}
QROOT=${QUEUE_ROOT:-/mnt/seiscomp_staging/eqserver_sweep_du_v2}
TRIED=${TRIED:-$HOME/v3_prod/du_v2_retried.txt}
touch "$TRIED"
cd "$REPO"
st() { echo "[retry] $(date -u +%FT%TZ) $*" >> "$STATUS"; }
export -f st
source "${LIB:-$HOME/du_v2_lib.sh}"

pass() {
  python3 - "$TRIED" "$QROOT" <<'PY' | while read -r K S Y; do
import glob, json, sys
tried = set(open(sys.argv[1]).read().split())
for q in sorted(glob.glob(sys.argv[2] + "_s*")):
    def recs(n):
        try:
            return [json.loads(l) for l in open(f"{q}/{n}")]
        except FileNotFoundError:
            return []
    done = {(r["sta"], int(r["year"])) for r in recs("convert_done.jsonl")}
    for r in recs("convert_failed.jsonl"):
        key = f'{r["sta"]}:{r["year"]}'
        if int(r["year"]) >= 2012 and (r["sta"], int(r["year"])) not in done \
                and key not in tried:
            tried.add(key)
            print(q.rsplit("_s", 1)[1], r["sta"], r["year"])
PY
    export Q="${QROOT}_s$K" K
    exec {fd}>"$LOCKS/station.$S.lock"
    if flock -n "$fd"; then
      echo "$S:$Y" >> "$TRIED"
      YS=" $(years_desc "$S") "; PREV=""
      [[ "$YS" == *" $((Y - 1)) "* ]] && PREV=$((Y - 1))
      st "retrying $S $Y (queue s$K)"
      if convert_year "$S" "$Y" "$PREV"; then st "retried $S $Y: done"
      else st "retried $S $Y: still failed — needs a human"; fi
      flock -u "$fd"
    fi
    exec {fd}>&-
  done
}
if [ "${1:-}" = "--once" ]; then pass; exit; fi
while true; do pass; sleep 600; done

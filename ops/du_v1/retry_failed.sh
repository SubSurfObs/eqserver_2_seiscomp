#!/bin/bash
# DU release v1: retry failed station-years (2026-10-31). Every 10 min, for each
# station-year recorded FAILED (and not since done) in a du_v1 queue whose
# station is not being converted right now: restore its file list (and the
# previous year's, for the Jan-1 tail) from the share archive if the cleaner
# moved it, then rerun that one year into the same queue. One retry per
# failure record; a unit that fails again stays failed for a human to look at.
set -u
REL=$HOME/projects/SubSurfObs_releases/du_v1
REPO=$REL/eqserver_2_seiscomp
PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
SEL=$HOME/v3_prod/selections_du_v1
ARCH=/mnt/seiscomp_staging/eqserver_v3_selections/du_v1
LOG=$HOME/v3_prod/logs_du_v1
STATUS=$HOME/v3_prod/du_v1.status
TRIED=$HOME/v3_prod/du_v1_retried.txt
touch "$TRIED"
st() { echo "[retry] $(date -u +%FT%TZ) $*" >> "$STATUS"; }

restore() {  # STA YEAR -> local selection present?
  local f="$SEL/DU.$1.$2.selection.sqlite" a="$ARCH/DU.$1/DU.$1.$2.selection.sqlite.gz"
  [ -f "$f" ] && return 0
  [ -f "$a" ] || return 1
  gunzip -c "$a" > "$f.partial" && mv "$f.partial" "$f"
}

while true; do
  todo=$(python3 - "$TRIED" <<'PY'
import glob, json, sys
tried = set(open(sys.argv[1]).read().split())
for q in sorted(glob.glob("/mnt/seiscomp_staging/eqserver_sweep_du_v1_s*")):
    k = q[-1]
    def recs(n):
        try:
            return [json.loads(l) for l in open(f"{q}/{n}")]
        except FileNotFoundError:
            return []
    done = {(r["sta"], int(r["year"])) for r in recs("convert_done.jsonl")}
    for r in recs("convert_failed.jsonl"):
        key = f'{r["sta"]}:{r["year"]}:{r["run_id"]}'
        if (r["sta"], int(r["year"])) not in done and key not in tried:
            print(k, r["sta"], r["year"], key)
PY
)
  while read -r K S Y KEY; do
    [ -z "${K:-}" ] && continue
    pgrep -f "run_production_convert.py --network DU --stations $S " >/dev/null && continue
    echo "$KEY" >> "$TRIED"
    if ! restore "$S" "$Y"; then st "$S $Y: no file list to restore — skipped"; continue; fi
    restore "$S" $((Y - 1)) || true
    st "retrying $S $Y (queue s$K)"
    (cd "$REPO" && $PY scan/run_production_convert.py --network DU --stations "$S" \
      --year-min "$Y" --year-max "$Y" --registry metadata/station_registry.yaml \
      --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
      --queue-dir "/mnt/seiscomp_staging/eqserver_sweep_du_v1_s$K" \
      --selections-dir "$SEL" --log-dir "$LOG" --workers 4 >> "$LOG/retry.log" 2>&1)
    st "retried $S $Y rc=$?"
  done <<< "$todo"
  sleep 600
done

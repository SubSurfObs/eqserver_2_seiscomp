#!/bin/bash
# DU release v2 batch, YEAR-AT-A-TIME (2026-11-01). Replaces du_v2_shard.sh,
# whose whole-station file-list builds (every year of the current AND next
# station at once, ~1 GB per year) overran the VM's local disk and stalled
# batches behind the disk guard for 5 h.
#
# Bounded local footprint per batch: before converting year Y it needs only
# the file lists for Y and Y-1 (Y-1 supplies the Jan-1 boundary tail); while
# Y converts, Y-2 is built in the background. Years run newest-first, so a
# list is finished with as soon as its own year is done, and sel_cleaner.sh
# moves it to the share. Peak: ~3 lists per batch (+2 for the next station's
# first years during the last year of this one).
#
# Usage: du_v2_peryear.sh <batch-number> <STA,STA,...>
set -u
K=$1
STATIONS=$2
REL=$HOME/projects/SubSurfObs_releases/du_v2
export REPO=$REL/eqserver_2_seiscomp
export PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
export SHIM=$REL/.pyshim
P=~/v3_prod
# Overridable for a scratch test run; defaults are production.
export SEL=${SEL:-$P/selections_du_v2}
Q=${QUEUE_ROOT:-/mnt/seiscomp_staging/eqserver_sweep_du_v2}_s$K
STAGING=${STAGING:-/mnt/seiscomp_staging/seiscomp_archive}
export LOG=${LOG:-$P/logs_du_v2}
export STATUS=${STATUS:-$P/du_v2.status}
export K
mkdir -p "$SEL" "$LOG" "$SHIM"
ln -sfn "$REL/sudspy/sudspy" "$SHIM/sudspy"
cd "$REPO"
st() { echo "[v2 b$K] $(date -u +%FT%TZ) $*" >> "$STATUS"; }

# Build one station-year's file list unless it exists (locally, or already
# converted and archived). Returns non-zero on failure.
compile_one() {  # STA YEAR
  local S=$1 Y=$2 out="$SEL/DU.$1.$2.selection.sqlite"
  [ -f "$out" ] && return 0
  (cd "$REPO" && PYTHONPATH="$SHIM" $PY scan/coverage_planner.py --net DU --sta "$S" \
     --year "$Y" --db ~/station_dbs/DU.$S.db --out "$out" > "$LOG/compile.$S.$Y.log" 2>&1)
  local rc=$?
  [ $rc -ne 0 ] && st "compile $S $Y FAILED rc=$rc"
  return $rc
}

years_desc() {  # plan years >= 2012, newest first
  (cd "$REPO" && $PY -c "
import yaml
p = yaml.safe_load(open('plans/DU/DU.$1.plan.yaml'))
print(' '.join(str(y) for y in sorted({int(e['start'][:4]) for e in p['epochs']}, reverse=True) if y >= 2012))")
}

done_years() {  # years of STA already done in this queue
  python3 - "$Q" "$1" <<'PY'
import json, sys
q, sta = sys.argv[1], sys.argv[2]
try:
    print(" ".join(sorted({str(json.loads(l)["year"]) for l in open(q + "/convert_done.jsonl")
                           if json.loads(l)["sta"] == sta})))
except FileNotFoundError:
    print("")
PY
}

st "mode: year-at-a-time file lists"
st "start: $STATIONS"
LIST=(${STATIONS//,/ })
for i in "${!LIST[@]}"; do
  S=${LIST[$i]}
  NEXT=${LIST[$((i + 1))]:-}
  YS=($(years_desc "$S"))
  DONE=" $(done_years "$S") "
  for j in "${!YS[@]}"; do
    Y=${YS[$j]}
    PREV=${YS[$((j + 1))]:-}
    [[ "$DONE" == *" $Y "* ]] && continue
    compile_one "$S" "$Y" || { st "skip $S $Y (no file list)"; continue; }
    [ -n "$PREV" ] && [ "$PREV" -eq $((Y - 1)) ] && compile_one "$S" "$PREV"
    # Background: the year after next, or the next station's newest year.
    bg=""
    AHEAD=${YS[$((j + 2))]:-}
    if [ -n "$AHEAD" ]; then
      compile_one "$S" "$AHEAD" & bg=$!
    elif [ -n "$NEXT" ]; then
      NY=($(years_desc "$NEXT"))
      [ -n "${NY[0]:-}" ] && { compile_one "$NEXT" "${NY[0]}" & bg=$!; }
    fi
    $PY scan/run_production_convert.py --network DU --stations "$S" \
      --year-min "$Y" --year-max "$Y" \
      --registry metadata/station_registry.yaml \
      --staging-sds "$STAGING" \
      --queue-dir "$Q" --selections-dir "$SEL" --log-dir "$LOG" --workers 4 \
      >> "$LOG/convert.b$K.log" 2>&1
    st "converted $S $Y rc=$?"
    [ -n "$bg" ] && wait "$bg"
  done
  st "station $S finished"
done
st "BATCH_DONE"

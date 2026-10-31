#!/bin/bash
# DU release v1 batch (2026-10-31). ALL code comes from the release folder
# ~/projects/SubSurfObs_releases/du_v1 (eqserver + sudspy + disk_to_sds at the
# Codex-approved pins); re-running DU later = rerun this script with a new
# release folder. File lists built 3 years at a time, the next station's
# lists built while the current one converts, lists archived (gz) to the
# share after each station.
# Usage: du_v1_batch.sh <batch-number> <STA,STA,...>
set -u
K=$1
STATIONS=$2
REL=$HOME/projects/SubSurfObs_releases/du_v1
export REPO=$REL/eqserver_2_seiscomp
export PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
export SHIM=$REL/.pyshim
P=~/v3_prod
export SEL=$P/selections_du_v1
Q=/mnt/seiscomp_staging/eqserver_sweep_du_v1_s$K
export LOG=$P/logs_du_v1
export STATUS=$P/du_v1.status
export K
mkdir -p "$SEL" "$LOG" "$SHIM"
ln -sfn "$REL/sudspy/sudspy" "$SHIM/sudspy"
cd "$REPO"
st() { echo "[v1 b$K] $(date -u +%FT%TZ) $*" >> "$STATUS"; }

compile_one() {  # STA YEAR — sudspy from the release folder (probes read files)
  local S=$1 Y=$2 out="$SEL/DU.$1.$2.selection.sqlite"
  [ -f "$out" ] && return 0
  cd "$REPO" && PYTHONPATH="$SHIM" $PY scan/coverage_planner.py --net DU --sta "$S" --year "$Y" \
    --db ~/station_dbs/DU.$S.db --out "$out" > "$LOG/compile.$S.$Y.log" 2>&1
  local rc=$?
  [ $rc -ne 0 ] && echo "[v1 b$K] $(date -u +%FT%TZ) compile $S $Y FAILED rc=$rc" >> "$STATUS"
  return 0
}
export -f compile_one

years_of() {
  (cd "$REPO" && $PY -c "
import yaml
p = yaml.safe_load(open('plans/DU/DU.$1.plan.yaml'))
print(' '.join(sorted({e['start'][:4] for e in p['epochs']})))")
}

compile_station() {
  local S=$1
  printf "%s\n" $(years_of "$S") | xargs -P 3 -I{} bash -c "compile_one $S {}"
  echo "[v1 b$K] $(date -u +%FT%TZ) compiled $S ($(years_of "$S"))" >> "$STATUS"
}

archive_station() {
  local S=$1 A=/mnt/seiscomp_staging/eqserver_v3_selections/du_v1/DU.$1
  mkdir -p "$A"
  for f in "$SEL"/DU.$S.*.selection.sqlite; do
    [ -f "$f" ] || continue
    gzip -c "$f" > "$A/$(basename "$f").gz" && rm -f "$f" "$f.deepok.json"
  done
  st "archived $S file lists -> $A"
}

st "start: $STATIONS"
LIST=(${STATIONS//,/ })
compile_station "${LIST[0]}"
for i in "${!LIST[@]}"; do
  S=${LIST[$i]}
  NEXT=${LIST[$((i + 1))]:-}
  bg=""
  if [ -n "$NEXT" ]; then
    compile_station "$NEXT" &
    bg=$!
  fi
  $PY scan/run_production_convert.py --network DU --stations "$S" \
    --year-min 2000 --year-max 2026 \
    --registry metadata/station_registry.yaml \
    --staging-sds /mnt/seiscomp_staging/seiscomp_archive \
    --queue-dir "$Q" --selections-dir "$SEL" --log-dir "$LOG" --workers 4 \
    >> "$LOG/convert.b$K.log" 2>&1
  st "converted $S rc=$?"
  archive_station "$S"
  [ -n "$bg" ] && wait "$bg"
done
st "BATCH_DONE"

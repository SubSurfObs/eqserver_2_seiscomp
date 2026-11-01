#!/bin/bash
# DU release v2 batch, YEAR-AT-A-TIME (2026-11-01; Codex 034f35c). Replaces
# du_v2_shard.sh, whose whole-station file-list builds overran the VM disk and
# stalled batches for 5 h. All control logic is in ops/du_v2/lib.sh (bounded,
# locked, failure-propagating); this file only walks the station list.
# Usage: du_v2_peryear.sh <batch-number> <STA,STA,...>
set -u
K=$1
STATIONS=$2
REL=$HOME/projects/SubSurfObs_releases/du_v2
export REPO=$REL/eqserver_2_seiscomp
export PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
export SHIM=$REL/.pyshim
P=~/v3_prod
# Overridable for scratch/synthetic tests; defaults are production.
export SEL=${SEL:-$P/selections_du_v2}
export Q=${QUEUE_ROOT:-/mnt/seiscomp_staging/eqserver_sweep_du_v2}_s$K
export STAGING=${STAGING:-/mnt/seiscomp_staging/seiscomp_archive}
export LOG=${LOG:-$P/logs_du_v2}
export STATUS=${STATUS:-$P/du_v2.status}
export K
mkdir -p "$SEL" "$LOG" "$SHIM"
ln -sfn "$REL/sudspy/sudspy" "$SHIM/sudspy"
cd "$REPO"
st() { echo "[v2 b$K] $(date -u +%FT%TZ) $*" >> "$STATUS"; }
export -f st
source "${LIB:-$HOME/du_v2_lib.sh}"

st "mode: year-at-a-time file lists"
st "start: $STATIONS"
LIST=(${STATIONS//,/ })
total=0
for i in "${!LIST[@]}"; do
  run_station "${LIST[$i]}" "${LIST[$((i + 1))]:-}"
  f=$?
  total=$((total + f))
  st "station ${LIST[$i]} finished ($f failed year(s))"
done
if [ $total -gt 0 ]; then
  st "BATCH_DONE_WITH_FAILURES ($total failed year(s))"
  exit 1
fi
st "BATCH_DONE"

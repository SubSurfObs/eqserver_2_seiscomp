#!/bin/bash
# Rebuild specific station-years with a given release, from FRESH file lists
# (own SEL/ARCH dirs, so no list built by an earlier release is restored),
# into a separate rebuild queue. Uses ops/du_v2/lib.sh controls (station lock,
# Jan-1 previous-year rule, disk admission, failure recording).
# Usage: rebuild_units.sh <release-dir-name> <queue-suffix> STA:YEAR [STA:YEAR ...]
set -u
RELNAME=$1; QS=$2; shift 2
REL=$HOME/projects/SubSurfObs_releases/$RELNAME
export REPO=$REL/eqserver_2_seiscomp
export PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
export SHIM=$REL/.pyshim
export SEL=$HOME/v3_prod/selections_${RELNAME}_rebuild
export ARCH=/mnt/seiscomp_staging/eqserver_v3_selections/${RELNAME}_rebuild
export Q=/mnt/seiscomp_staging/eqserver_sweep_du_v2_rebuild_$QS
export STAGING=/mnt/seiscomp_staging/seiscomp_archive
export LOG=$HOME/v3_prod/logs_du_v2
export STATUS=$HOME/v3_prod/du_v2.status
export K=rebuild_$QS
mkdir -p "$SEL" "$SHIM"; ln -sfn "$REL/sudspy/sudspy" "$SHIM/sudspy"
cd "$REPO"
st() { echo "[rebuild $RELNAME] $(date -u +%FT%TZ) $*" >> "$STATUS"; }
export -f st
source "$HOME/du_v2_lib.sh"
fails=0
for U in "$@"; do
  S=${U%:*}; Y=${U#*:}
  exec {fd}>"$LOCKS/station.$S.lock"; flock "$fd"
  ALL=" $(plan_years_all "$S") "; PREV=""
  [[ "$ALL" == *" $((Y - 1)) "* ]] && PREV=$((Y - 1))
  st "rebuilding $S $Y (prev: ${PREV:-none})"
  if convert_year "$S" "$Y" "$PREV"; then st "rebuilt $S $Y"
  else st "FAILED rebuild $S $Y"; fails=$((fails + 1)); fi
  flock -u "$fd"; exec {fd}>&-
done
st "REBUILD_DONE ($fails failed)"
[ $fails -eq 0 ]

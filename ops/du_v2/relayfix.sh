#!/bin/bash
# Relay-file fix (Codex 80a4460, 2026-11-02). The old Level-1 indexes dropped
# "<STA>.relay.dmx.gz" files as wrong-station. Census: CLV2 2017, MRAT 2017 +
# 2019, PLMR 2017, STR2 2019 (no other excluded class is revived by the
# current parser). Station DBs re-indexed by the release-v2 parser into
# ~/station_dbs_relayfix. This script, once the re-index has finished:
#   1. swaps in each new DB when its station is idle (no conversion running,
#      station lock free), keeping the old DB under ~/station_dbs_pre_relayfix;
#   2. rebuilds MRAT 2017 and 2019 — already converted from the old DB, now
#      held — into the queue eqserver_sweep_du_v2_rebuild_s0, from FRESH file
#      lists (own SEL/ARCH dirs, so no old-index list can be restored).
# CLV2, PLMR and STR2 are not yet converted in v2: their batches will compile
# from the new DB when they reach them.
set -u
REL=$HOME/projects/SubSurfObs_releases/du_v2
export REPO=$REL/eqserver_2_seiscomp
export PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
export SHIM=$REL/.pyshim
export SEL=$HOME/v3_prod/selections_du_v2_rebuild
export ARCH=/mnt/seiscomp_staging/eqserver_v3_selections/du_v2_rebuild
export Q=/mnt/seiscomp_staging/eqserver_sweep_du_v2_rebuild_s0
export STAGING=/mnt/seiscomp_staging/seiscomp_archive
export LOG=$HOME/v3_prod/logs_du_v2
export STATUS=$HOME/v3_prod/du_v2.status
export K=rebuild
NEW=$HOME/station_dbs_relayfix
OLD=$HOME/station_dbs_pre_relayfix
mkdir -p "$SEL" "$OLD"
cd "$REPO"
st() { echo "[relayfix] $(date -u +%FT%TZ) $*" >> "$STATUS"; }
export -f st
source "$HOME/du_v2_lib.sh"

# CLV2, PLMR, STR2 are idle now: hold their station locks from the start so
# no batch can compile them from the OLD DB before the swap (a batch reaching
# one simply waits on its lock). MRAT is locked once its batch releases it.
declare -A FD
for S in CLV2 PLMR STR2; do
  exec {fd}>"$LOCKS/station.$S.lock"; flock "$fd"; FD[$S]=$fd
done
st "holding CLV2 PLMR STR2 until their re-indexed DBs are in"

swap() {  # STA (caller holds its lock)
  mv "$HOME/station_dbs/DU.$1.db" "$OLD/" && cp "$NEW/DU.$1.db" "$HOME/station_dbs/DU.$1.db"
  rm -f "$HOME/v3_prod/selections_du_v2/DU.$1."*.selection.sqlite   # built from the old DB
  st "swapped in re-indexed DB for $1"
}

until [ -f "$NEW/DU.CLV2.db" ] && [ -f "$NEW/DU.PLMR.db" ] && [ -f "$NEW/DU.STR2.db" ] \
      && [ -f "$NEW/DU.MRAT.db" ] && ! pgrep -f "scan/level1.py --stations CLV2,MRA[T]" >/dev/null; do
  sleep 60
done
for S in CLV2 PLMR STR2; do swap "$S"; flock -u "${FD[$S]}"; done

exec {SFD}>"$LOCKS/station.MRAT.lock"; flock "$SFD"          # waits for MRAT's batch
while pgrep -f "run_production_convert.py --network DU --stations MRAT " >/dev/null; do sleep 60; done
swap MRAT
ALL=" $(plan_years_all MRAT) "
for Y in 2017 2019; do
  PREV=""; [[ "$ALL" == *" $((Y - 1)) "* ]] && PREV=$((Y - 1))
  st "rebuilding MRAT $Y from the re-indexed DB (prev: ${PREV:-none})"
  if convert_year MRAT "$Y" "$PREV"; then st "rebuilt MRAT $Y"; else st "FAILED rebuild MRAT $Y"; fi
done
flock -u "$SFD"
st "RELAYFIX_DONE"

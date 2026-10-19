#!/bin/bash
set -u
PY=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
V3=/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp_v3
PROV=/mnt/seiscomp_staging/eqserver_provenance/VW/2026-06-10/station_dbs
AB=~/v3_test/ab; rm -rf $AB; mkdir -p $AB/sel_dbs
cd $V3
declare -A DB PLAN DAY YEAR
DB[BEST]=$PROV/VW.BEST.db;  PLAN[BEST]=plans/VW/VW.BEST.plan.yaml;  DAY[BEST]=2022-06-15; YEAR[BEST]=2022
DB[STBK]=$PROV/VW.STBK.db;  PLAN[STBK]=plans/VW/VW.STBK.plan.yaml;  DAY[STBK]=2023-01-08; YEAR[STBK]=2023
DB[DDWB]=$PROV/VW.DDWB.db;  PLAN[DDWB]=plans/VW/VW.DDWB.plan.yaml;  DAY[DDWB]=2021-06-15; YEAR[DDWB]=2021
for STA in BEST STBK DDWB; do
  echo "=== $STA ${DAY[$STA]} ==="
  SEL=$AB/sel_dbs/VW.$STA.${YEAR[$STA]}.selection.sqlite
  $PY scan/coverage_planner.py --net VW --sta $STA --year ${YEAR[$STA]} --db ${DB[$STA]} --out $SEL || { echo "COMPILE FAIL $STA"; continue; }
  echo "${DAY[$STA]}" > $AB/dates_$STA.txt
  for MODE in legacy selection; do
    OUT=$AB/${STA}_$MODE; mkdir -p $OUT
    EXTRA=""
    [ $MODE = selection ] && EXTRA="--selection $SEL"
    $PY scan/phase3_driver.py ${DB[$STA]} ${PLAN[$STA]} --registry metadata/station_registry.yaml \
      --staging-sds $OUT --commit --dates-file $AB/dates_$STA.txt --workers 1 $EXTRA 2>&1 | tail -3
  done
  echo "--- byte comparison $STA ---"
  diff -r $AB/${STA}_legacy $AB/${STA}_selection > $AB/${STA}.diff 2>&1 && echo "BYTE-IDENTICAL" || { echo "DIFFERS:"; head -5 $AB/${STA}.diff; }
done
echo "AB DONE"

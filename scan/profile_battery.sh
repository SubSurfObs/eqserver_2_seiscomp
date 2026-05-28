#!/bin/bash
# Profile battery for Phase 3 conversion performance.
#
# Runs three test phases sequentially:
#   1. Worker-count sweep per cohort (~25 min)
#   2. Multi-station pool grid (~30 min)
#   3. Long real conversion (~90-120 min)
#
# Each phase writes a per-test log + appends a summary line to RESULTS.csv.
# Final SUMMARY.md is generated at the end.
#
# Easy-cases-only: phase3_driver skips flagged_days from each plan, so nothing
# requiring user review is ever attempted. Dangling cases are left to the user.
#
# Usage:
#   bash profile_battery.sh

set -u  # but NOT -e — we want to continue past individual test failures
export TZ=AEST

REPO=/home/unimelb.edu.au/dsand/projects/SubSurfObs/eqserver_2_seiscomp
VENV=/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/.venv/bin/python3
STATION_DBS=/home/unimelb.edu.au/dsand/station_dbs
PLANS=/tmp/plans_vw
REGISTRY=$REPO/metadata/station_registry.yaml
RESULTS=/tmp/profile_results
STAGING=/tmp/profile_staging

mkdir -p $RESULTS
echo "test,station,cohort,workers,pool_size,days,wallclock_s,bytes_written,status_counts" > $RESULTS/RESULTS.csv

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a $RESULTS/MAIN.log ; }
log_to() { local t=$1; shift; echo "$*" >> $RESULTS/$t.log ; }

reset_staging() {
    rm -rf $STAGING
    mkdir -p $STAGING
}

# ============================================================
# PHASE 1: Worker-count sweep per cohort
# Same 10 days × 3 stations × workers ∈ {1, 4, 8, 16}
# ============================================================

log "==== PHASE 1: Worker-count sweep per cohort ===="

# Pick representative stations per cohort. OUTU = EchoPro clean baseline,
# STBK = Gecko clean baseline, DDBE = Minimus per-channel.
declare -a SWEEP=(
    "OUTU echopro 2024-01-01 2024-01-10"
    "STBK gecko   2024-01-01 2024-01-10"
    "DDBE minimus 2020-01-01 2020-01-10"
)

for spec in "${SWEEP[@]}"; do
    read -r STA COHORT START END <<<"$spec"
    for W in 1 4 8 16; do
        reset_staging
        log "PHASE1 $STA $COHORT workers=$W days=$START..$END"
        t0=$(date +%s)
        $VENV -u $REPO/scan/phase3_driver.py \
            $STATION_DBS/VW.$STA.db \
            $PLANS/VW.$STA.plan.yaml \
            --registry $REGISTRY \
            --staging-sds $STAGING \
            --start-date $START --end-date $END \
            --workers $W --commit \
            >$RESULTS/p1_${STA}_w${W}.log 2>&1
        t1=$(date +%s)
        wall=$((t1-t0))
        bytes=$(du -sb $STAGING 2>/dev/null | awk '{print $1}')
        bytes=${bytes:-0}
        status=$(grep "status counts" $RESULTS/p1_${STA}_w${W}.log | tr -d '\n' | tr -d ' ')
        echo "phase1,$STA,$COHORT,$W,1,10,$wall,$bytes,\"$status\"" >> $RESULTS/RESULTS.csv
        log "  done in ${wall}s; ${bytes} bytes; $status"
    done
done

# ============================================================
# PHASE 2: Multi-station pool grid
# 4 EchoPro stations × 30 days at (pool, workers) ∈ {(1,16),(2,8),(4,4),(8,2)}
# All combinations use 16 total NFS readers (pool * workers = 16).
# ============================================================

log "==== PHASE 2: Multi-station pool grid ===="

POOL_STATIONS="OUTU,HOLS,NARR,WPNH"
START2=2024-02-01
END2=2024-03-02

for combo in "1 16" "2 8" "4 4" "8 2"; do
    read -r P W <<<"$combo"
    reset_staging
    log "PHASE2 stations=$POOL_STATIONS pool=$P per-station-workers=$W"
    t0=$(date +%s)
    $VENV -u $REPO/scan/run_phase3_pool.py \
        --station-dbs $STATION_DBS \
        --plans $PLANS \
        --registry $REGISTRY \
        --staging-sds $STAGING \
        --stations $POOL_STATIONS \
        --start-date $START2 --end-date $END2 \
        --pool-size $P --per-station-workers $W \
        --commit \
        >$RESULTS/p2_p${P}_w${W}.log 2>&1
    t1=$(date +%s)
    wall=$((t1-t0))
    bytes=$(du -sb $STAGING 2>/dev/null | awk '{print $1}')
    bytes=${bytes:-0}
    echo "phase2,$POOL_STATIONS,echopro,$W,$P,30,$wall,$bytes,\"\"" >> $RESULTS/RESULTS.csv
    log "  done in ${wall}s; ${bytes} bytes"
done

# ============================================================
# PHASE 3: Long real conversion
# 8 stations × 100 days at pool=4 workers=4 (16 NFS readers, conservative default).
# Produces actual SDS we can inspect afterward.
# ============================================================

log "==== PHASE 3: Long real conversion ===="

reset_staging
LONG_STATIONS="OUTU,HOLS,STBK,WDSD,DDBE,DDWB,SGWU,TRPU"
START3=2024-04-01
END3=2024-07-10

log "PHASE3 stations=$LONG_STATIONS pool=4 per-station-workers=4 100-day window"
t0=$(date +%s)
$VENV -u $REPO/scan/run_phase3_pool.py \
    --station-dbs $STATION_DBS \
    --plans $PLANS \
    --registry $REGISTRY \
    --staging-sds $STAGING \
    --stations $LONG_STATIONS \
    --start-date $START3 --end-date $END3 \
    --pool-size 4 --per-station-workers 4 \
    --commit \
    >$RESULTS/p3_long.log 2>&1
t1=$(date +%s)
wall=$((t1-t0))
bytes=$(du -sb $STAGING 2>/dev/null | awk '{print $1}')
bytes=${bytes:-0}
sds_files=$(find $STAGING -type f -name "*.D.*" 2>/dev/null | wc -l)
echo "phase3,$LONG_STATIONS,mixed,4,4,100,$wall,$bytes,sds_files=$sds_files" >> $RESULTS/RESULTS.csv
log "PHASE3 done in ${wall}s; ${bytes} bytes; ${sds_files} SDS files"

# ============================================================
# SUMMARY
# ============================================================

log "==== Generating SUMMARY.md ===="

cat >$RESULTS/SUMMARY.md <<EOF
# Profile battery summary

Generated: $(date)

## RESULTS.csv (raw)
\`\`\`
$(cat $RESULTS/RESULTS.csv)
\`\`\`

## Worker-count sweep (phase 1)

Per-cohort throughput at varying workers. Look for the knee where adding
more workers stops helping = NFS-IOPS ceiling for that cohort.

\`\`\`
station  cohort   workers  wallclock_s  bytes  days_per_sec
$(awk -F, '/^phase1/{printf "%-7s  %-7s  %7s  %11s  %12s  %5.2f\n", $2, $3, $4, $7, $8, 10/$7}' $RESULTS/RESULTS.csv)
\`\`\`

## Pool grid (phase 2)

Same total NFS readers (pool × workers = 16), different scheduling.
The fastest wallclock wins — tells us whether to spread across stations
or stack workers within one.

\`\`\`
pool  per-station-workers  wallclock_s  bytes
$(awk -F, '/^phase2/{printf "%4s  %19s  %11s  %s\n", $5, $4, $7, $8}' $RESULTS/RESULTS.csv)
\`\`\`

## Long run (phase 3)

8 stations × 100-day window through the multi-station pool. Anchors the
real "stations per hour" production number.

\`\`\`
$(awk -F, '/^phase3/{printf "Wallclock: %ss\nBytes:     %s\nDetails:   %s\n", $7, $8, $9}' $RESULTS/RESULTS.csv)
\`\`\`

## Per-test logs
- /tmp/profile_results/p1_*.log (worker sweep)
- /tmp/profile_results/p2_*.log (pool grid)
- /tmp/profile_results/p3_long.log (long run)
- /tmp/profile_results/MAIN.log (orchestration)

## SDS output (preserved from phase 3)
- /tmp/profile_staging — inspect with \`obspy.read\` or \`ls /tmp/profile_staging/2024/VW/<STA>/...\`
EOF

log "==== ALL DONE ===="
log "Summary: cat $RESULTS/SUMMARY.md"
log "Raw CSV: $RESULTS/RESULTS.csv"

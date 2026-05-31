#!/usr/bin/env bash
# regenerate_plans.sh — regenerate per-station plan YAMLs for one network.
#
# Each VW/DU/etc. station has its own Level-1 manifest DB at
# ~/station_dbs/<NET>.<STA>.db (built by scan/level1.py). plan_generator.py
# takes a single-DB argument and emits one plan; this wrapper just loops it
# over every DB for the requested network and lands the results in the
# in-git plans/<NET>/ tree.
#
# Usage:
#   scan/regenerate_plans.sh <NET> [-j PARALLEL] [--db-dir DIR] [--out DIR] [--registry PATH]
#
# Defaults:
#   PARALLEL = 1            (serial; bump to ~4 on the staging VM for ~3x speedup)
#   --db-dir = ~/station_dbs
#   --out    = <repo>/plans/<NET>
#   --registry = <repo>/metadata/station_registry.yaml
#
# Wall-clock reference (per PROGRESS.md): ~1 h for all 41 VW DBs at PARALLEL=1.
# HOLS (2.5 GB) is the slow tail (~few min); short-history stations (e.g.
# MOSU at 49 days) complete in seconds. PARALLEL=4 is safe — each DB is
# independent — and brings the wallclock down meaningfully.
#
# After this script lands the new plans in plans/<NET>/, review the diff,
# `git add plans/<NET>/`, and commit.

set -euo pipefail

NET="${1:-}"
if [[ -z "$NET" ]]; then
    echo "usage: $0 <NET> [-j PARALLEL] [--db-dir DIR] [--out DIR] [--registry PATH]" >&2
    exit 2
fi
shift

PARALLEL=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DB_DIR="${HOME}/station_dbs"
OUT="${REPO_ROOT}/plans/${NET}"
REGISTRY="${REPO_ROOT}/metadata/station_registry.yaml"
PYTHON="${PYTHON:-python3}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -j)        PARALLEL="$2"; shift 2 ;;
        --db-dir)  DB_DIR="$2"; shift 2 ;;
        --out)     OUT="$2"; shift 2 ;;
        --registry) REGISTRY="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$OUT"

dbs=("$DB_DIR/$NET".*.db)
if [[ ! -e "${dbs[0]}" ]]; then
    echo "no DBs found at $DB_DIR/$NET.*.db" >&2
    exit 1
fi

echo "[regen] network=$NET parallel=$PARALLEL"
echo "[regen]   db-dir=$DB_DIR (${#dbs[@]} DBs)"
echo "[regen]   out=$OUT"
echo "[regen]   registry=$REGISTRY"

t0=$(date +%s)

# Build the per-DB command list and pipe to xargs.
# Each DB is independent, so parallelism is safe.
printf '%s\n' "${dbs[@]}" | xargs -P "$PARALLEL" -I {} bash -c '
    db="$1"
    sta=$(basename "$db" .db | cut -d. -f2-)
    t=$(date +%s)
    "'"$PYTHON"'" "'"$SCRIPT_DIR"'/plan_generator.py" \
        "$db" --registry "'"$REGISTRY"'" --out "'"$OUT"'" \
        --stations "$sta" 2>&1 | tail -1
    echo "[regen]   $sta done in $(( $(date +%s) - t ))s"
' _ {}

elapsed=$(( $(date +%s) - t0 ))
echo
echo "[regen] done in ${elapsed}s. Status breakdown across plans/$NET/:"
for s in ok defer_conversion; do
    n=$(grep -l "^status: $s\$" "$OUT"/*.plan.yaml 2>/dev/null | wc -l | tr -d ' ')
    printf '  %-20s %s\n' "$s" "$n"
done
# Anything else (legacy BLOCKED / needs_review) should be 0 after this commit.
other=$(grep "^status:" "$OUT"/*.plan.yaml 2>/dev/null \
        | awk '{print $2}' | sort -u | grep -vE '^(ok|defer_conversion)$' || true)
if [[ -n "$other" ]]; then
    echo "  WARNING — unexpected statuses: $other"
fi

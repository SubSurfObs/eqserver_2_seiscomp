#!/bin/bash
# Shared control logic for the DU release-v2 launcher and retry worker
# (Codex 034f35c). Sourced, not run. Every function is overridable for the
# synthetic control tests in ops/du_v2/test_control.sh.
#
# Guarantees:
#  1. a year is converted only when its own file list AND the previous year's
#     (Jan-1 boundary tail) are present — built, or restored from the share;
#  2. any build/restore/convert failure is recorded in the queue and makes the
#     batch end BATCH_DONE_WITH_FAILURES (exit 1), never silently skipped;
#  3. one builder per file (build lock), one worker per station (station
#     lock, held by the parent and inherited by its conversion child);
#  4. a GLOBAL bound: at most $MAX_BUILDS concurrent file-list builds, a
#     required build starts only above $REQ_FREE_GB free, a speculative
#     prefetch only above $SPEC_FREE_GB (else skipped — it becomes required
#     later). Existing conversions are never paused.

: "${LOCKS:=$HOME/v3_prod/locks}"
: "${ARCH:=/mnt/seiscomp_staging/eqserver_v3_selections/du_v2}"
: "${MAX_BUILDS:=2}"
: "${REQ_FREE_GB:=35}"
: "${SPEC_FREE_GB:=45}"
export LOCKS ARCH
mkdir -p "$LOCKS"

free_gb() { df --output=avail -BG / | tail -1 | tr -dc 0-9; }

# Real builder; tests override.
planner() {  # STA YEAR OUT
  (cd "$REPO" && PYTHONPATH="$SHIM" $PY scan/coverage_planner.py --net DU --sta "$1" \
     --year "$2" --db ~/station_dbs/DU.$1.db --out "$3" > "$LOG/compile.$1.$2.log" 2>&1)
}

record_failure() {  # STA YEAR REASON  -> queue convert_failed.jsonl
  python3 - "$Q" "$1" "$2" "$3" <<'PY'
import datetime, json, os, sys
q, sta, year, reason = sys.argv[1:5]
os.makedirs(q, exist_ok=True)
with open(os.path.join(q, "convert_failed.jsonl"), "a") as f:
    f.write(json.dumps({"action": "failed", "net": "DU", "sta": sta, "year": int(year),
        "run_id": f"launcher_{sta}_{year}_{datetime.datetime.utcnow():%Y%m%dT%H%M%SZ}",
        "rc": None, "reason": reason,
        "ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"}) + "\n")
PY
}

restore_list() {  # STA YEAR -> 0 if the local list exists afterwards
  local f="$SEL/DU.$1.$2.selection.sqlite" a="$ARCH/DU.$1/DU.$1.$2.selection.sqlite.gz"
  [ -f "$f" ] && return 0
  [ -f "$a" ] || return 1
  gunzip -c "$a" > "$f.partial" && mv "$f.partial" "$f"
}

# Wait for a global build slot; runs "$@" holding it.
with_build_slot() {
  while true; do
    for i in $(seq 0 $((MAX_BUILDS - 1))); do
      flock -n -E 75 "$LOCKS/build_slot.$i" "$@"
      local rc=$?
      [ $rc -eq 75 ] && continue   # slot busy: try the next one
      return $rc                   # the command ran: its own result
    done
    sleep 5
  done
}

_build_locked() {  # STA YEAR — under the per-file build lock
  local out="$SEL/DU.$1.$2.selection.sqlite"
  [ -f "$out" ] && return 0
  restore_list "$1" "$2" && return 0
  planner "$1" "$2" "$out" && [ -f "$out" ]
}
export -f _build_locked planner restore_list

# ensure_list STA YEAR required|speculative -> 0 when the list is present.
ensure_list() {
  local S=$1 Y=$2 kind=$3 out="$SEL/DU.$1.$2.selection.sqlite"
  [ -f "$out" ] && return 0
  restore_list "$S" "$Y" && return 0
  if [ "$kind" = speculative ]; then
    [ "$(free_gb)" -gt "$SPEC_FREE_GB" ] || return 1
  else
    local said=0
    while [ "$(free_gb)" -le "$REQ_FREE_GB" ]; do
      [ $said = 0 ] && st "waiting for disk (${REQ_FREE_GB} GB) to build $S $Y" && said=1
      sleep 60
    done
  fi
  # build slot first, then the per-file lock (a second builder of the same
  # file waits on the file lock and then finds the list present)
  with_build_slot flock "$LOCKS/build.$S.$Y.lock" bash -c "_build_locked $S $Y"
}

# convert_year STA YEAR PREVYEAR|"" -> runs one year; 0 = converted ok.
convert_year() {
  local S=$1 Y=$2 PREV=$3
  if ! ensure_list "$S" "$Y" required; then
    record_failure "$S" "$Y" "file list build failed"; st "FAILED $S $Y: file list"; return 1
  fi
  if [ -n "$PREV" ] && ! ensure_list "$S" "$PREV" required; then
    record_failure "$S" "$Y" "previous-year file list ($PREV) unavailable: Jan-1 tail would be lost"
    st "FAILED $S $Y: no $PREV list for the Jan-1 tail"; return 1
  fi
  run_convert "$S" "$Y"
  # Success = the unit is recorded DONE (the wrapper records a failure
  # itself otherwise; its exit code alone is not the contract).
  [[ " $(done_years "$S") " == *" $Y "* ]]
}

run_convert() {  # real conversion; tests override
  $PY scan/run_production_convert.py --network DU --stations "$1" \
    --year-min "$2" --year-max "$2" \
    --registry metadata/station_registry.yaml \
    --staging-sds "$STAGING" \
    --queue-dir "$Q" --selections-dir "$SEL" --log-dir "$LOG" --workers 4 \
    >> "$LOG/convert.b$K.log" 2>&1
}

years_desc() {  # plan years >= 2012, newest first
  (cd "$REPO" && $PY -c "
import yaml
p = yaml.safe_load(open('plans/DU/DU.$1.plan.yaml'))
print(' '.join(str(y) for y in sorted({int(e['start'][:4]) for e in p['epochs']}, reverse=True) if y >= 2012))")
}

done_years() {  # years of STA recorded done in this queue
  python3 - "$Q" "$1" <<'PY'
import json, sys
q, sta = sys.argv[1], sys.argv[2]
try:
    rs = [json.loads(l) for l in open(q + "/convert_done.jsonl")]
except FileNotFoundError:
    rs = []
print(" ".join(sorted({str(r["year"]) for r in rs if r["sta"] == sta})))
PY
}

# Run every pending year of one station, newest first, under its lock.
# Echoes nothing; returns the number of failed years (capped at 255).
run_station() {  # STA NEXT_STA|""
  local S=$1 NEXT=$2 fails=0
  exec {SFD}>"$LOCKS/station.$S.lock"
  flock "$SFD"
  local YS=($(years_desc "$S")) DONE=" $(done_years "$S") "
  for j in "${!YS[@]}"; do
    local Y=${YS[$j]} PREV=${YS[$((j + 1))]:-} AHEAD=${YS[$((j + 2))]:-} bg=""
    [[ "$DONE" == *" $Y "* ]] && continue
    [ -n "$PREV" ] && [ "$PREV" -ne $((Y - 1)) ] && PREV=""
    if [ -n "$AHEAD" ]; then
      ensure_list "$S" "$AHEAD" speculative & bg=$!
    elif [ -n "$NEXT" ]; then
      local NY=($(years_desc "$NEXT"))
      [ -n "${NY[0]:-}" ] && { ensure_list "$NEXT" "${NY[0]}" speculative & bg=$!; }
    fi
    if convert_year "$S" "$Y" "$PREV"; then st "converted $S $Y"
    else fails=$((fails + 1)); fi
    [ -n "$bg" ] && wait "$bg"
  done
  flock -u "$SFD"; exec {SFD}>&-
  return $(( fails > 255 ? 255 : fails ))
}

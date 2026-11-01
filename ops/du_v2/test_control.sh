#!/bin/bash
# Synthetic control tests for ops/du_v2/lib.sh + sel_cleaner.sh (Codex
# 034f35c). No archive, no real builds or conversions: the planner and the
# converter are stand-ins that record what they were asked to do.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export SEL=$T/sel LOG=$T/log STATUS=$T/status Q=$T/q_s0 K=0 LOCKS=$T/locks \
       ARCH=$T/arch STAGING=$T/sds REPO=$T PY=python3 SHIM=$T
mkdir -p "$SEL" "$LOG" "$Q" "$ARCH" "$LOCKS"
FAILS=0
ok()   { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
st()   { echo "[t] $*" >> "$STATUS"; }
export -f st
echo 100 > "$T/free"
source "$HERE/lib.sh"

free_gb() { cat "$T/free"; }
years_desc() { cat "$T/years.$1" 2>/dev/null; }
planner() {  # stand-in builder: fails for listed STA:YEAR, tracks concurrency
  local now; now=$(( $(cat "$T/active" 2>/dev/null || echo 0) + 1 )); echo $now > "$T/active"
  [ $now -gt "$(cat "$T/maxactive" 2>/dev/null || echo 0)" ] && echo $now > "$T/maxactive"
  echo "$1:$2" >> "$T/built"
  sleep "${BUILD_SLEEP:-0}"
  echo $(( $(cat "$T/active") - 1 )) > "$T/active"
  grep -qx "$1:$2" "$T/failbuild" 2>/dev/null && return 1
  touch "$3"
}
run_convert() {  # stand-in conversion: records whether prev list existed
  local p="$SEL/DU.$1.$(( $2 - 1 )).selection.sqlite"
  echo "$1:$2 prev=$([ -f "$p" ] && echo yes || echo no)" >> "$T/converted"
  echo "{\"sta\": \"$1\", \"year\": $2}" >> "$Q/convert_done.jsonl"
}
export -f free_gb years_desc planner run_convert
export T

# 1. previous-year list cannot be built -> year NOT converted, failure recorded
echo "2025 2024" > "$T/years.A"; echo "A:2024" > "$T/failbuild"
run_station A ""; rc=$?
grep -q "^A:2025" "$T/converted" 2>/dev/null && bad "Jan-1: 2025 converted without 2024 list" \
  || ok "Jan-1: 2025 not converted when 2024 list fails"
grep -q '"sta": "A", "year": 2025.*previous-year' "$Q/convert_failed.jsonl" \
  && ok "Jan-1 failure recorded in queue" || bad "Jan-1 failure not recorded"
[ $rc -gt 0 ] && ok "run_station reports failures (rc=$rc)" || bad "run_station rc=0 despite failures"

# 2. a year's own list fails -> recorded, later years still run, batch rc != 0
: > "$T/failbuild"; echo "B:2023" > "$T/failbuild"; echo "2023 2021" > "$T/years.B"
run_station B ""; rc=$?
grep -q '"sta": "B", "year": 2023.*file list build failed' "$Q/convert_failed.jsonl" \
  && ok "own-list failure recorded" || bad "own-list failure not recorded"
grep -q "^B:2021" "$T/converted" && ok "other years continue" || bad "other years stopped"
[ $rc -gt 0 ] && ok "failure propagates (rc=$rc)" || bad "failure swallowed"
bash -c 'sed -e "s|^REL=.*|REL=$T|" -e "s|^cd \"\$REPO\"|cd $T|" -e "/ln -sfn/d" '"$HERE"'/du_v2_peryear.sh > $T/launcher.sh' 
( export -f planner run_convert years_desc free_gb
  echo "2020" > "$T/years.C"; echo "C:2020" >> "$T/failbuild"
  LIB=$T/lib_test.sh; { cat "$HERE/lib.sh"; declare -f planner run_convert years_desc free_gb; } > $T/lib_test.sh
  LIB=$T/lib_test.sh QUEUE_ROOT=$T/qq STATUS=$T/status2 bash $T/launcher.sh 0 C > /dev/null 2>&1 ); rc=$?
[ $rc -ne 0 ] && grep -q BATCH_DONE_WITH_FAILURES "$T/status2" \
  && ok "launcher ends BATCH_DONE_WITH_FAILURES, exit $rc" || bad "launcher exit $rc / no failure marker"

# 3. two builders of the same file -> built once
: > "$T/built"; : > "$T/failbuild"
BUILD_SLEEP=2 ensure_list D 2020 required & BUILD_SLEEP=2 ensure_list D 2020 required & wait
[ "$(grep -c '^D:2020$' "$T/built")" = 1 ] && ok "same file built once under the build lock" \
  || bad "same file built $(grep -c '^D:2020$' "$T/built") times"

# 4. global bound: 5 concurrent builds of different files -> <= MAX_BUILDS at once
echo 0 > "$T/active"; echo 0 > "$T/maxactive"
for y in 2011 2012 2013 2014 2015; do BUILD_SLEEP=2 ensure_list E $y required & done; wait
m=$(cat "$T/maxactive")
[ "$m" -le "$MAX_BUILDS" ] && ok "at most $m concurrent builds (limit $MAX_BUILDS)" \
  || bad "$m concurrent builds exceeded limit $MAX_BUILDS"

# 5. disk admission: speculative skipped below SPEC_FREE_GB; required waits below REQ_FREE_GB
echo 40 > "$T/free"
ensure_list F 2020 speculative && bad "speculative build ran at 40 GB" \
  || ok "speculative prefetch skipped at 40 GB (< $SPEC_FREE_GB)"
echo 30 > "$T/free"
( ensure_list F 2021 required ) & pid=$!
sleep 3
[ -f "$SEL/DU.F.2021.selection.sqlite" ] && bad "required build ran at 30 GB" \
  || ok "required build waits at 30 GB (< $REQ_FREE_GB), conversions untouched"
echo 50 > "$T/free"; sleep 62; wait $pid
[ -f "$SEL/DU.F.2021.selection.sqlite" ] && ok "required build proceeds once space returns" \
  || bad "required build never proceeded"

# 6. cleaner: keeps a done year still needed as a pending year's previous year
mkdir -p "$T/plans" "$T/cq_s0"
printf 'station: G\nepochs:\n- start: "2025-01-01"\n- start: "2024-01-01"\n- start: "2023-01-01"\n' > "$T/plans/DU.G.plan.yaml"
for y in 2023 2024 2025; do echo "sel$y" > "$SEL/DU.G.$y.selection.sqlite"; done
echo '{"sta": "G", "year": 2025}' > "$T/cq_s0/convert_done.jsonl"
echo '{"sta": "G", "year": 2023}' >> "$T/cq_s0/convert_done.jsonl"   # 2024 still pending
CLEAN_ONLY_V2=1 CLEAN_SEL=$SEL CLEAN_QGLOB="$T/cq_s*" CLEAN_ARCH=$T/carch CLEAN_PLANS=$T/plans \
  HOME=$T bash "$HERE/sel_cleaner.sh" --once > /dev/null 2>&1
[ -f "$SEL/DU.G.2025.selection.sqlite" ] && bad "done 2025 kept (nothing needs it)" \
  || ok "done 2025 archived (2026 not pending)"
[ -f "$SEL/DU.G.2023.selection.sqlite" ] && ok "done 2023 KEPT: pending 2024 needs it for Jan 1" \
  || bad "done 2023 removed though pending 2024 needs it"
[ -f "$T/carch/DU.G/DU.G.2025.selection.sqlite.gz" ] && ok "archived copy verified on 'share'" \
  || bad "no archived copy"

echo
[ $FAILS -eq 0 ] && { echo "all control tests passed"; exit 0; }
echo "$FAILS control test(s) FAILED"; exit 1

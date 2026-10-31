#!/bin/bash
# Disk-space brake v3 (2026-10-31). Pauses ONLY the processes that consume
# local disk — file-list builds (coverage_planner) and the Level-1 indexer —
# never the converters: freezing a converter trips its hang watchdog and
# aborts the station-year (five 2026 units lost that way at 04:46 UTC).
# Every 60 s: free < 25 GB -> SIGSTOP those processes; free > 35 GB -> SIGCONT.
# The per-year file-list cleaner (sel_cleaner.sh) frees space meanwhile.
set -u
S=~/v3_prod/du_v1.status
paused=0
while true; do
  free_gb=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
  pids=$(pgrep -f "scan/coverage_planner.py|scan/level1.py")
  if [ "$free_gb" -lt 25 ] && [ -n "$pids" ]; then
    kill -STOP $pids 2>/dev/null
    [ "$paused" = 0 ] && echo "[guard] $(date -u +%FT%TZ) PAUSED file-list builds — only ${free_gb} GB free (conversions keep running)" >> "$S"
    paused=1
  elif [ "$paused" = 1 ] && [ "$free_gb" -gt 35 ]; then
    [ -n "$pids" ] && kill -CONT $pids 2>/dev/null
    paused=0
    echo "[guard] $(date -u +%FT%TZ) RESUMED file-list builds — ${free_gb} GB free" >> "$S"
  fi
  sleep 60
done

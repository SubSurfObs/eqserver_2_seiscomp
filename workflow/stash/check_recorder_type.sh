#!/bin/bash
# ==========================================
# checkRecorderType.sh
# Determines if a day directory contains Echo or Gecko recorder data.
# Logic:
#   1. Assume Echo by default.
#   2. Count Echo files (*.dmx, *.dmx.gz)
#   3. Count Gecko files (*.ms, *.ms.zip)
#   4. If no Gecko files => Echo
#   5. If no Echo files  => Gecko
#   6. If both types exist:
#        - If total >= threshold → choose higher count
#        - Else → fallback to previous day's type
# ==========================================

day_dir="$1"
prev_type="$2"
threshold="$3"

if [ -z "$day_dir" ]; then
  echo "Usage: $0 /path/to/day_dir [previous_type] [threshold]" >&2
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="$SCRIPT_DIR/config.txt"

# --- Load config if available ---
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
fi

# --- Configurable parameter (default 100 if not passed) ---
MIN_FILE_THRESHOLD="${threshold:-${MIN_FILE_THRESHOLD:-100}}"

# --- Count Echo and Gecko files ---
dmx_count=$(find "$day_dir" -maxdepth 1 -type f \( -name "*.dmx" -o -name "*.dmx.gz" \) | wc -l)
ms_count=$(find "$day_dir" -maxdepth 1 -type f \( -name "*.ms" -o -name "*.ms.zip" \) | wc -l)
total=$((dmx_count + ms_count))

# --- Logging for verbose mode ---
log() { [ "${VERBOSE_MODE:-0}" -eq 1 ] && echo "$@" >&2; }
log "[$(basename "$day_dir")] Echo count: $dmx_count, Gecko count: $ms_count, Total: $total"

# --- Decision logic ---
if [ "$ms_count" -eq 0 ] && [ "$dmx_count" -gt 0 ]; then
  log "No Gecko files found → Echo"
  echo "echo"
  exit 0
fi

if [ "$dmx_count" -eq 0 ] && [ "$ms_count" -gt 0 ]; then
  log "No Echo files found → Gecko"
  echo "gecko"
  exit 0
fi

# --- Both file types present ---
if [ "$total" -ge "$MIN_FILE_THRESHOLD" ]; then
  if [ "$dmx_count" -gt "$ms_count" ]; then
    log "Both file types present, Echo count higher (≥ threshold)"
    echo "echo"
  else
    log "Both file types present, Gecko count higher (≥ threshold)"
    echo "gecko"
  fi
else
  log "Both file types present but total < threshold → fallback to previous type"
  echo "${prev_type:-echo}"
fi

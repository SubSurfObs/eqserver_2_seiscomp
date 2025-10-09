#!/bin/bash
# ================================================================
# process_archive_echo.sh
# Master driver: recursively process a station's archive (year/month/day)
# Applies process_day_echo.sh or process_day_gecko.sh as needed.
# ================================================================

set -euo pipefail

# ------------- Parse input -------------
VERBOSE=0
if [ "${1:-}" == "--verbose" ]; then
  VERBOSE=1
  shift
fi

input_dir="${1:-}"
sds_archive="${2:-}"
temp_base="${3:-${TMPDIR:-/tmp}}"

if [ -z "$input_dir" ] || [ -z "$sds_archive" ]; then
  echo "Usage: $0 [--verbose] /path/to/archive /path/to/sds_archive [temp_base]" >&2
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="$SCRIPT_DIR/config.txt"
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

# ------------- Determine station name -------------
# Try from path: /archive/STATION/YYYY/MM/DD
station=$(basename "$(dirname "$(dirname "$(dirname "$input_dir")")")")
# Fallback: parse from filename if not in a standard path
if [[ ! "$station" =~ ^[A-Z0-9]{4,5}$ ]]; then
  first_file=$(find "$input_dir" -type f -print -quit)
  station=$(basename "$first_file" | grep -oE '[A-Z0-9]{4,5}(?=\.)')
fi
station=${station:-UNKNOWN}

# ------------- Setup logging -------------
LOG_BASE="$SCRIPT_DIR/logs/$station"
mkdir -p "$LOG_BASE/crash_reports"
master_log="$LOG_BASE/master.log"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$master_log" >&2; }

# ------------- Crash trap -------------
trap '{
  err_code=$?;
  echo "ERROR: Crash under $current_day_dir (exit $err_code)" >> "$LOG_BASE/crash_reports/crash_$(date -Iseconds).log";
  if [ -n "${current_log_file:-}" ] && [ -f "$current_log_file" ]; then
    echo "--- Last 30 lines of $current_log_file ---" >> "$LOG_BASE/crash_reports/crash_$(date -Iseconds).log";
    tail -n 30 "$current_log_file" >> "$LOG_BASE/crash_reports/crash_$(date -Iseconds).log";
  fi
  exit $err_code;
}' ERR

# ------------- Detect level (YEAR/MONTH/DAY) -------------
detect_level() {
  local dir="$1"
  local files=$(find "$dir" -maxdepth 1 -type f \( -name "*.dmx" -o -name "*.dmx.gz" -o -name "*.ms" -o -name "*.ms.zip" \) | wc -l)
  if [ "$files" -gt 0 ]; then
    echo "DAY"; return
  fi
  local subdirs=$(find "$dir" -mindepth 1 -maxdepth 1 -type d | wc -l)
  local two_digit=$(find "$dir" -mindepth 1 -maxdepth 1 -type d | grep -E '/[0-9]{2}$' | wc -l)
  if [ "$two_digit" -ge 28 ]; then
    echo "MONTH"
  elif [ "$two_digit" -le 12 ] && [ "$subdirs" -gt 0 ]; then
    echo "YEAR"
  else
    echo "UNKNOWN"
  fi
}

level=$(detect_level "$input_dir")

# ------------- Main recursion -------------
if [ "$level" == "YEAR" ]; then
  log "Detected YEAR level: $(basename "$input_dir")"
  for month_dir in "$input_dir"/*/; do
    [ -d "$month_dir" ] || continue
    "$0" ${VERBOSE:+--verbose} "$month_dir" "$sds_archive" "$temp_base"
  done

elif [ "$level" == "MONTH" ]; then
  log "Detected MONTH level: $(basename "$input_dir")"
  prev_type="echo"
  for day_dir in "$input_dir"/*/; do
    [ -d "$day_dir" ] || continue
    current_day_dir="$day_dir"
    day_name=$(basename "$day_dir")
    current_log_file="$LOG_BASE/${day_name}_day.log"

    exec > >(stdbuf -oL tee -a "$current_log_file") 2>&1

    log "Processing day: $day_name"

    recorder_type=$("$SCRIPT_DIR/checkRecorderType.sh" "$day_dir" "$prev_type")
    prev_type="$recorder_type"
    log "Recorder type: $recorder_type"

    # Skip if already processed in SDS
    if find "$sds_archive" -type f -name "*${day_name}*" | grep -q .; then
      log "Skipping $day_name — already present in SDS."
      continue
    fi

    if [ "$recorder_type" == "echo" ]; then
      "$SCRIPT_DIR/process_day_echo.sh" "$day_dir" "$sds_archive" "$temp_base"
    else
      "$SCRIPT_DIR/process_day_gecko.sh" "$day_dir" "$sds_archive" "$temp_base"
    fi

    log "Finished day: $day_name"
    echo "$(date -Iseconds) | $day_name | $recorder_type | Status: OK" >> "$master_log"
  done

elif [ "$level" == "DAY" ]; then
  # Direct day-level call
  current_day_dir="$input_dir"
  day_name=$(basename "$input_dir")
  current_log_file="$LOG_BASE/${day_name}_day.log"
  exec > >(stdbuf -oL tee -a "$current_log_file") 2>&1

  log "Detected single DAY directory"
  recorder_type=$("$SCRIPT_DIR/checkRecorderType.sh" "$input_dir")
  log "Recorder type: $recorder_type"

  if [ "$recorder_type" == "echo" ]; then
    "$SCRIPT_DIR/process_day_echo.sh" "$input_dir" "$sds_archive" "$temp_base"
  else
    "$SCRIPT_DIR/process_day_gecko.sh" "$input_dir" "$sds_archive" "$temp_base"
  fi

  log "Completed single day run."

else
  log "Unknown directory level for $input_dir — skipping."
fi

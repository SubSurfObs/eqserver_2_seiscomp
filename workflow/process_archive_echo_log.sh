#!/bin/bash
# ================================================================
# process_archive_echo_log.sh
# Recursively process an EQServer archive (year/month/day)
# using the appropriate day-level converter (Echo or Gecko).
# Logs are written in flat structure with unique YYYY-MM-DD filenames.
# Each day now gets its own separate log file.
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
station=$(basename "$(dirname "$(dirname "$(dirname "$input_dir")")")")
if [[ ! "$station" =~ ^[A-Z0-9]{4,5}$ ]]; then
  first_file=$(find "$input_dir" -type f -print -quit 2>/dev/null || true)
  station=$(basename "$first_file" | grep -oE '[A-Z0-9]{4,5}(?=\.)' || echo "UNKNOWN")
fi
station=${station:-UNKNOWN}

# ------------- Setup logging -------------
LOG_BASE="$SCRIPT_DIR/logs/$station"
mkdir -p "$LOG_BASE/crash_reports"
master_log="$LOG_BASE/master.log"
log() { echo "[$(date -Iseconds)] $*" | tee -a "$master_log" >&2; }

# ------------- Crash trap -------------
current_day_dir=""
current_log_file=""
trap '{
  err_code=$?;
  crash_file="$LOG_BASE/crash_reports/crash_$(date -Iseconds).log"
  echo "ERROR: Crash under ${current_day_dir:-N/A} (exit $err_code)" >> "$crash_file"
  if [ -n "${current_log_file:-}" ] && [ -f "$current_log_file" ]; then
    echo "--- Last 30 lines of $current_log_file ---" >> "$crash_file"
    tail -n 30 "$current_log_file" >> "$crash_file"
  fi
  exit $err_code;
}' ERR

# ------------- Detect level (YEAR/MONTH/DAY) -------------
detect_level() {
  local dir="$1"
  local dir_name parent_name
  dir_name=$(basename "$dir")
  parent_name=$(basename "$(dirname "$dir")")

  local file_count
  file_count=$(find "$dir" -maxdepth 1 -type f \
    \( -name "*.dmx" -o -name "*.dmx.gz" -o -name "*.ms" -o -name "*.ms.zip" \) \
    | wc -l)

  if [ "$file_count" -gt 0 ]; then
    echo "DAY"
    return
  fi

  local subdirs subdir_count two_digit_count
  subdirs=$(find "$dir" -mindepth 1 -maxdepth 1 -type d)
  subdir_count=$(echo "$subdirs" | wc -l)
  two_digit_count=$(echo "$subdirs" | grep -E '/[0-9]{2}$' | wc -l)

  # --- Smart detection ---
  if [[ "$parent_name" =~ ^[0-9]{4}$ ]] && [[ "$dir_name" =~ ^(0[1-9]|1[0-2])$ ]]; then
    echo "MONTH"
    return
  fi
  if [ "$two_digit_count" -ge 28 ]; then
    echo "MONTH"; return
  fi
  if [ "$two_digit_count" -le 12 ] && [ "$subdir_count" -gt 0 ]; then
    echo "YEAR"; return
  fi

  echo "UNKNOWN"
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
  year_name=$(basename "$(dirname "$input_dir")")
  month_name=$(basename "$input_dir")

  # Save original stdout/stderr
  exec 3>&1 4>&2

  for day_dir in "$input_dir"/*/; do
    [ -d "$day_dir" ] || continue
    current_day_dir="$day_dir"
    day_name=$(basename "$day_dir")

    # Unique per-day log file
    current_log_file="$LOG_BASE/${station}_${year_name}-${month_name}-${day_name}_day.log"

    # Redirect output for this day only
    exec > >(stdbuf -oL tee -a "$current_log_file") 2>&1

    log "Processing day: $day_name"

    recorder_type=$("$SCRIPT_DIR/check_recorder_type.sh" "$day_dir" "$prev_type")
    prev_type="$recorder_type"
    log "Detected recorder type: $recorder_type"

    if find "$sds_archive" -type f -name "*${day_name}*" | grep -q .; then
      log "Skipping $day_name — already present in SDS."
      # Restore stdout/stderr
      exec >&3 2>&4
      continue
    fi

    if [ "$recorder_type" == "echo" ]; then
      "$SCRIPT_DIR/process_day_echo.sh" "$day_dir" "$sds_archive" "$temp_base"
    else
      "$SCRIPT_DIR/process_day_gecko.sh" "$day_dir" "$sds_archive" "$temp_base"
    fi

    log "Finished processing day: $day_name"
    echo "$(date -Iseconds) | ${year_name}-${month_name}-${day_name} | $recorder_type | Status: OK" >> "$master_log"

    # Restore stdout/stderr before next day
    exec >&3 2>&4
  done

elif [ "$level" == "DAY" ]; then
  current_day_dir="$input_dir"
  day_name=$(basename "$input_dir")
  year_name=$(basename "$(dirname "$(dirname "$input_dir")")")
  month_name=$(basename "$(dirname "$input_dir")")
  current_log_file="$LOG_BASE/${station}_${year_name}-${month_name}-${day_name}_day.log"

  # Redirect only for this day
  exec > >(stdbuf -oL tee -a "$current_log_file") 2>&1
  log "Detected single DAY directory"

  recorder_type=$("$SCRIPT_DIR/check_recorder_type.sh" "$input_dir")
  log "Detected recorder type: $recorder_type"

  if [ "$recorder_type" == "echo" ]; then
    "$SCRIPT_DIR/process_day_echo.sh" "$input_dir" "$sds_archive" "$temp_base"
  else
    "$SCRIPT_DIR/process_day_gecko.sh" "$input_dir" "$sds_archive" "$temp_base"
  fi

  log "Completed single day run."

else
  log "Unknown directory level for $input_dir — skipping."
fi

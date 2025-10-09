#!/bin/bash
# ==========================================
# process_day.sh
# Converts daily DMX files to MiniSEED and imports into an SDS archive
# ==========================================



# --- Parse flags ---
VERBOSE=0
if [ "$1" == "--verbose" ] || [ "$1" == "-v" ]; then
  VERBOSE=1
  shift
fi
export VERBOSE_MODE=$VERBOSE


# --- Load config ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="$SCRIPT_DIR/config.txt"

if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
else
  echo "Error: Config file not found: $CONFIG_FILE"
  exit 1
fi

# --- Arguments ---
day_dir="$1"
sds_archive="$2"
temp_base="$3"

if [ -z "$day_dir" ] || [ -z "$sds_archive" ] || [ -z "$temp_base" ]; then
  echo "Usage: $0 [--verbose|-v] /path/to/day_dir /path/to/sds_archive /path/to/temp_base"
  exit 1
fi

# --- Helper for logging ---
log() { [ $VERBOSE -eq 1 ] && echo "$@"; }

# --- Setup temp dirs ---
mkdir -p "$temp_base" "$sds_archive"
temp_day_dir="$temp_base/$(basename "$day_dir")"
mkdir -p "$temp_day_dir"

log "Processing day: $(basename "$day_dir")"
log "Temporary directory: $temp_day_dir"

# --- Find DMX files using external helper ---
dmx_files=$("$SCRIPT_DIR/find_files_echo.sh" "$day_dir")
dmx_count=$(echo "$dmx_files" | grep -c .)

if [ "$dmx_count" -eq 0 ]; then
  log "No DMX files found in $day_dir"
  rm -rf "$temp_day_dir"
  exit 0
fi

log "Found $dmx_count DMX files"

# --- Determine available CPUs dynamically ---
nproc_cmd=$(command -v nproc)
if [ -n "$nproc_cmd" ]; then
  nproc_val=$($nproc_cmd)
else
  nproc_val=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
fi
log "Using $nproc_val parallel jobs"

# --- Convert DMX → MiniSEED ---
log "Converting DMX files..."
echo "$dmx_files" | parallel -j "$nproc_val" $EQCONVERT_PATH {} -f miniseed -w "$temp_day_dir/{/.}.mseed" >/dev/null 2>&1
log "Conversion complete."

# --- Sort and merge MiniSEED files ---
if ls "$temp_day_dir"/*.mseed &> /dev/null; then
  log "Sorting and merging MiniSEED files..."
  scmssort -u -E "$temp_day_dir"/*.mseed > "$temp_day_dir/sorted.mseed"
else
  echo "Error: No MiniSEED files produced."
  rm -rf "$temp_day_dir"
  exit 1
fi

# --- Generate remap and channel spec strings ---
output=$("$SCRIPT_DIR/generate_remap_string.sh" "$temp_day_dir/sorted.mseed")
remap_string=$(echo "$output" | head -n 1)
channel_spec_string=$(echo "$output" | tail -n 1)

log "Remap string: $remap_string"
log "Channel spec string: $channel_spec_string"

# --- Import into SDS archive ---
if [ -f "$temp_day_dir/sorted.mseed" ]; then
  if [ -n "$remap_string" ] && [ -n "$channel_spec_string" ]; then
    log "Running SCART import..."
    scart -I "$temp_day_dir/sorted.mseed" \
          --with-filecheck "$sds_archive" \
          -c "$channel_spec_string" \
          --rename "$remap_string" >/dev/null 2>&1
    scart_status=$?
    [ $scart_status -ne 0 ] && echo "Warning: scart exited with non-zero status ($scart_status)" >&2
  else
    echo "Error: Remap string or channel spec string is empty before SCART call"
    rm -rf "$temp_day_dir"
    exit 1
  fi
else
  echo "Error: sorted.mseed file not found"
  rm -rf "$temp_day_dir"
  exit 1
fi

# --- Cleanup ---
log "Cleaning up temporary directory..."
rm -rf "$temp_day_dir"

[ $VERBOSE -eq 1 ] && echo "Finished processing day: $(basename "$day_dir")"
exit 0

#!/bin/bash
# process_day_echo.sh
# Usage:
#   ./process_day_echo.sh /path/to/day_dir /path/to/sds_archive [temp_base] [--verbose]

# --- Parse arguments ---
day_dir="$1"
sds_archive="$2"
temp_base="${3:-/tmp}"
verbose=false

# Handle optional args (temp_base + --verbose)
for arg in "${@:4}"; do
    if [[ "$arg" == "--verbose" ]]; then
        verbose=true
    fi
done

# --- Logging helper ---
log() {
    if $verbose; then
        echo "$@"
    fi
}

# --- Basic validation ---
if [ -z "$day_dir" ] || [ -z "$sds_archive" ]; then
    echo "Usage: $0 /path/to/day_dir /path/to/sds_archive [temp_base] [--verbose]"
    exit 1
fi

if [ ! -d "$day_dir" ]; then
    echo "ERROR: Day directory not found: $day_dir"
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# --- Load configuration ---
CONFIG_FILE="$SCRIPT_DIR/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "ERROR: Missing config.txt in $SCRIPT_DIR"
    exit 1
fi

# --- Prepare directories ---
mkdir -p "$temp_base"
mkdir -p "$sds_archive"

day_name=$(basename "$day_dir")
temp_day_dir="$(mktemp -d "$temp_base/echo_process_${day_name}_XXXXXX")"
log "Temporary working directory: $temp_day_dir"

# --- Find DMX files using external helper ---
dmx_files=$("$SCRIPT_DIR/find_files_echo.sh" "$day_dir")

if [ -z "$dmx_files" ]; then
    log "No DMX files found in $day_dir"
    rm -rf "$temp_day_dir"
    exit 0
fi

log "Found $(echo "$dmx_files" | wc -l) DMX files"

# --- Convert DMX → MiniSEED ---
log "Converting DMX files..."
echo "$dmx_files" | parallel -j "$(nproc)" $EQCONVERT_PATH {} -f miniseed -w "$temp_day_dir/{/.}.mseed" >/dev/null 2>&1

# --- Sort and merge MiniSEED files ---
log "Sorting and merging MiniSEED files..."
scmssort -u -E "$temp_day_dir"/*.mseed > "$temp_day_dir/sorted.mseed"

# --- Generate remap and channel spec strings ---
log "Generating remap and channel spec strings..."
output=$("$SCRIPT_DIR/generate_remap_string.sh" "$temp_day_dir/sorted.mseed")
remap_string=$(echo "$output" | head -n 1)
channel_spec_string=$(echo "$output" | tail -n 1)

log "Remap string: $remap_string"
log "Channel spec string: $channel_spec_string"

# --- Import into SDS archive ---
if [ -f "$temp_day_dir/sorted.mseed" ]; then
  if [ -n "$remap_string" ] && [ -n "$channel_spec_string" ]; then
    log "Running SCART import..."
    if $verbose; then
      scart -I "$temp_day_dir/sorted.mseed" \
            --with-filecheck "$sds_archive" \
            -c "$channel_spec_string" \
            --rename "$remap_string"
    else
      scart -I "$temp_day_dir/sorted.mseed" \
            --with-filecheck "$sds_archive" \
            -c "$channel_spec_string" \
            --rename "$remap_string" >/dev/null 2>&1
    fi
  else
    echo "Error: Remap string or channel spec string is empty before SCART call" >&2
    rm -rf "$temp_day_dir"
    exit 1
  fi
fi

log "Finished processing day: $day_name"

# --- Cleanup ---
rm -rf "$temp_day_dir"

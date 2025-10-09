#!/bin/bash
# process_day_gecko.sh
# Usage:
#   ./process_day_gecko.sh /path/to/day_dir /path/to/sds_archive [temp_base] [--verbose]
#
# Example:
#   ./process_day_gecko.sh /data/repository/archive/DDSW/continuous/2022/05/10 test_sds/
#   ./process_day_gecko.sh /data/repository/archive/DDSW/continuous/2022/05/10 test_sds/ /tmp --verbose

# --- Parse arguments ---
day_dir="$1"
sds_archive="$2"
temp_base="/tmp"
verbose=false

# Handle optional args (temp_base + --verbose)
for arg in "${@:3}"; do
    if [[ "$arg" == "--verbose" ]]; then
        verbose=true
    elif [[ -n "$arg" ]]; then
        temp_base="$arg"
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
temp_day_dir="$(mktemp -d "$temp_base/gecko_process_${day_name}_XXXXXX")"
log "Temporary working directory: $temp_day_dir"

# --- Copy relevant files into temp dir ---
"$SCRIPT_DIR/find_files_gecko.sh" "$day_dir" | while IFS= read -r f; do
    [ -e "$f" ] || continue
    cp "$f" "$temp_day_dir/"
done

# --- Simple parallel unzip (quiet and forgiving) ---
zip_files=($(find "$temp_day_dir" -maxdepth 1 -type f -name "*.zip"))
zip_count=${#zip_files[@]}

if [ "$zip_count" -gt 0 ]; then
    log "Unzipping $zip_count files in parallel..."
    export temp_day_dir
    printf "%s\n" "${zip_files[@]}" | parallel -j 4 --no-notice '
        unzip -o -q "{}" -d "$temp_day_dir" >/dev/null 2>&1 || true
        rm -f "{}"
    '
else
    log "No ZIP files to extract."
fi


# --- Concatenate all .ms files ---
tmp_full=$(mktemp)
find "$temp_day_dir" -maxdepth 1 -type f -name "*.ms" -print0 | sort -z | xargs -0 cat > "$tmp_full"
mv "$tmp_full" "$temp_day_dir/full.ms"

# --- Sort into MiniSEED ---
log "Sorting..."
scmssort -u -E "$temp_day_dir/full.ms" > "$temp_day_dir/sorted.mseed"

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
    exit 1
  fi
fi

log "Finished processing day: $day_name"

# --- Cleanup ---
rm -rf "$temp_day_dir"

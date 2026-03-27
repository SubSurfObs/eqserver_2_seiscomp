#!/bin/bash
# process_day_gecko.sh
# Usage: ./process_day_gecko.sh /path/to/day_dir /path/to/sds_archive [temp_base]
# Example:
#   ./process_day_gecko.sh /data/repository/archive/DDSW/continuous/2022/05/10 test_sds temp_dir

day_dir="$1"           # Full path to the day directory
sds_archive="$2"       # SDS archive path
temp_base="${3:-/tmp}" # Optional temp base (default /tmp)

if [ -z "$day_dir" ] || [ -z "$sds_archive" ]; then
    echo "Usage: $0 /path/to/day_dir /path/to/sds_archive [temp_base]"
    exit 1
fi

# Ensure day_dir exists
if [ ! -d "$day_dir" ]; then
    echo "ERROR: Day directory not found: $day_dir"
    exit 1
fi

# Determine script location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Load config
CONFIG_FILE="$SCRIPT_DIR/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "ERROR: Missing config.txt in $SCRIPT_DIR"
    exit 1
fi

mkdir -p "$temp_base"
mkdir -p "$sds_archive"

# Temporary directory for this day
day_name=$(basename "$day_dir")
temp_day_dir="$(mktemp -d "$temp_base/gecko_process_${day_name}_XXXXXX")"

echo "Temporary working directory: $temp_day_dir"

# Copy and unzip selected files
"$SCRIPT_DIR/find_files_gecko.sh" "$day_dir" | while IFS= read -r f; do
    [ -e "$f" ] || continue
    cp "$f" "$temp_day_dir/"
done

# Unzip all .zip files inside temp dir
find "$temp_day_dir" -maxdepth 1 -type f -name "*.zip" | while IFS= read -r z; do
    echo "Unzipping: $z"
    unzip -o -q "$z" -d "$temp_day_dir"
    rm "$z"
done

# Concatenate and sort
tmp_full=$(mktemp)
find "$temp_day_dir" -maxdepth 1 -type f -name "*.ms" -print0 | sort -z | xargs -0 cat > "$tmp_full"
mv "$tmp_full" "$temp_day_dir/full.ms"

echo "Sorting..."
scmssort -u -E "$temp_day_dir/full.ms" > "$temp_day_dir/sorted.mseed"

# --- Generate remap and channel spec strings ---
echo "Generating remap and channel spec strings..."
output=$("$SCRIPT_DIR/generate_remap_string.sh" "$temp_day_dir/sorted.mseed")
remap_string=$(echo "$output" | head -n 1)
channel_spec_string=$(echo "$output" | tail -n 1)

echo "Remap string: $remap_string"
echo "Channel spec string: $channel_spec_string"

# --- Import into SDS archive ---
if [ -f "$temp_day_dir/sorted.mseed" ]; then
  if [ -n "$remap_string" ] && [ -n "$channel_spec_string" ]; then
    echo "Running SCART import..."
    scart -I "$temp_day_dir/sorted.mseed" \
          --with-filecheck "$sds_archive" \
          -c "$channel_spec_string" \
          --rename "$remap_string"
  else
    echo "Error: Remap string or channel spec string is empty before SCART call" >&2
    exit 1
  fi
fi

echo "Finished processing day: $day_name"

# CLEANUP
rm -rf "$temp_day_dir"

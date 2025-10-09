#!/bin/bash

# Load config file
CONFIG_FILE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/config.txt"
if [ -f "$CONFIG_FILE" ]; then
  source "$CONFIG_FILE"
else
  echo "Error: Config file not found: $CONFIG_FILE"
  exit 1
fi

# Usage: ./process_day.sh /path/to/day_dir /path/to/sds_archive /path/to/temp_base
day_dir="$1"
sds_archive="$2"
temp_base="$3"

if [ -z "$day_dir" ] || [ -z "$sds_archive" ] || [ -z "$temp_base" ]; then
  echo "Usage: $0 /path/to/day_dir /path/to/sds_archive /path/to/temp_base"
  exit 1
fi

# Ensure day_dir exists
if [ ! -d "$day_dir" ]; then
  echo "ERROR: Day directory not found: $day_dir"
  exit 1
fi

# Create temp directory
mkdir -p "$temp_base"

# Count dmx files, excluding certain strings
dmx_files=$(find "$day_dir" -maxdepth 1 -type f -name "*.dmx" | grep -vE "$IGNORE_STRINGS")
dmx_count=$(echo "$dmx_files" | wc -l)
echo "Found $dmx_count dmx files"

# Run eqconvert on each dmx file in parallel
echo "$dmx_files" | parallel -j 16 java -jar ~/software/eqconvert.jar {} -f miniseed -w "$temp_base/{/.}.mseed"


# Sort and merge MiniSEED files
scmssort -u -E "$temp_base"/*.mseed > "$temp_base/sorted.mseed"

# Generate remap string and channel spec string
#output=$("$SCRIPT_DIR/generate_remap_string.sh" "$temp_base/sorted.mseed")
output=$(./generate_remap_string.sh "$temp_base/sorted.mseed")

remap_string=$(echo "$output" | head -n 1)
channel_spec_string=$(echo "$output" | tail -n 1)
echo "Remap string: $remap_string"
echo "Channel spec string: $channel_spec_string"



# --- Import into SDS archive ---
if [ -f "$temp_base/sorted.mseed" ]; then
  if [ -n "$remap_string" ] && [ -n "$channel_spec_string" ]; then
    echo "Running SCART import..." 
    scart -I "$temp_base/sorted.mseed" \
          --with-filecheck "$sds_archive" \
          -c "$channel_spec_string" \
          --rename "$remap_string"

    scart_status=$?
    if [ $scart_status -ne 0 ]; then
      echo "Warning: scart exited with non-zero status ($scart_status)" >&2
    fi
  else
    echo "Error: Remap string or channel spec string is empty before SCART call"
    echo "remap_string='$remap_string'"
    echo "channel_spec_string='$channel_spec_string'"
    exit 1
  fi
else
  echo "Error: sorted.mseed file not found"
  exit 1
fi

echo "Processing complete"

#!/bin/bash
# ==========================================
# generate_remap_string.sh
# Generates remap and channel spec strings for SCART
# ==========================================

# Load config
CONFIG_FILE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "Error: Config file not found: $CONFIG_FILE" >&2
    exit 1
fi

# Check arguments
if [ $# -ne 1 ]; then
    echo "Usage: $0 <miniseed_file>" >&2
    exit 1
fi

miniseed_file=$1

# Run SCART to get streams
output=$(scart --print-streams -I "$miniseed_file" --test 2>&1)

stream_count=$(echo "$output" | grep "#   streams:" | awk '{print $3}')
echo "Number of streams: $stream_count" >&2

# Extract full channel names
channel_strings=$(echo "$output" | awk '/^[A-Z][A-Z]\./ {print $1}')
unique_channel_strings=$(echo "$channel_strings" | sort -u)

echo "Unique channel strings before filtering:" >&2
echo "$unique_channel_strings" >&2

# If more than 3 channels, exclude those with 'N' in the middle
selected_channels="$unique_channel_strings"
if [ $(echo "$unique_channel_strings" | wc -l) -gt 3 ]; then
    echo "More than 3 channels detected, applying filter..." >&2
    selected_channels=$(echo "$unique_channel_strings" | grep -v "N")
fi

# Keep only first 3 if still more than 3
selected_channels=$(echo "$selected_channels" | head -3)
echo "Selected channels for remap:" >&2
echo "$selected_channels" >&2

# Determine base channel (first 2 characters of first channel code)
first_channel=$(echo "$selected_channels" | head -1)
channel_code=$(echo "$first_channel" | cut -d '.' -f4)
base_channel=${channel_code:0:2}

# Construct channel spec string
channel_spec_string="${base_channel}?"

# Build remap string
remap_string=""
for channel in $selected_channels; do
    network=$(echo "$channel" | cut -d '.' -f1)
    station=$(echo "$channel" | cut -d '.' -f2)
    location=$(echo "$channel" | cut -d '.' -f3)
    channel_code=$(echo "$channel" | cut -d '.' -f4)

    # Extract component (last char), robust for 2- or 3-char codes
    if [ ${#channel_code} -ge 3 ]; then
        component=${channel_code: -1}
    else
        component="Z"
    fi

    new_channel="${NET}.${station}.${LOC}.${CH}${component}"
    remap_string+="${channel}:${new_channel},"
done

# Remove trailing comma
remap_string=${remap_string%,}

# Output results
echo "$remap_string"
echo "$channel_spec_string"

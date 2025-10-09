#!/bin/bash
# ==========================================
# generate_remap_string.sh
# Generates remap and channel spec strings for SCART import
# Quiet by default; verbose mode via VERBOSE_MODE=1
# ==========================================

# --- Verbose logging helper ---
log() { [ "${VERBOSE_MODE:-0}" -eq 1 ] && echo "$@" >&2; }

# --- Load config ---
CONFIG_FILE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )/config.txt"
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "Error: Config file not found: $CONFIG_FILE" >&2
    exit 1
fi

# --- Arguments ---
if [ $# -ne 1 ]; then
    echo "Usage: $0 <miniseed_file>" >&2
    exit 1
fi

miniseed_file=$1

# --- Run scart to list streams ---
output=$(scart --print-streams -I "$miniseed_file" --test 2>&1)
stream_count=$(echo "$output" | grep "#   streams:" | awk '{print $3}')
log "Number of streams: $stream_count"

# --- Extract and filter channel list ---
channel_strings=$(echo "$output" | awk '/^[A-Z][A-Z]\./ {print $1}')
unique_channel_strings=$(echo "$channel_strings" | sort -u)
log "Unique channel strings before filtering:"
log "$unique_channel_strings"

# --- Handle cases with more than 3 channels ---
selected_channels="$unique_channel_strings"
if [ "$(echo "$unique_channel_strings" | wc -l)" -gt 3 ]; then
    log "More than 3 channels detected, filtering those with 'N' in the middle..."
    selected_channels=$(echo "$unique_channel_strings" | grep -vE '.N.')
fi

# --- Keep only 3 and sort for consistent order ---
selected_channels=$(echo "$selected_channels" | grep -E '[ZEN]$' | sort -t '.' -k4,4 | head -3)
log "Selected channels for remap:"
log "$selected_channels"

# --- Derive base channel for -c spec ---
first_channel=$(echo "$selected_channels" | head -1)
channel_code=$(echo "$first_channel" | cut -d '.' -f4)
base_channel=${channel_code:0:2}
channel_spec_string="${base_channel}?"

# --- Build remap string ---
remap_string=""
for channel in $selected_channels; do
    network=$(echo "$channel" | cut -d '.' -f1)
    station=$(echo "$channel" | cut -d '.' -f2)
    location=$(echo "$channel" | cut -d '.' -f3)
    channel_code=$(echo "$channel" | cut -d '.' -f4)

    if [ ${#channel_code} -ge 3 ]; then
        component=${channel_code: -1}
    else
        component="Z"
    fi

    new_channel="${NET}.${station}.${LOC}.${CH}${component}"
    remap_string+="${channel}:${new_channel},"
done

# --- Clean up trailing comma ---
remap_string=${remap_string%,}

# --- Verbose summary ---
log "Remap string: $remap_string"
log "Channel spec string: $channel_spec_string"

# --- Output final results for process_day.sh ---
echo "$remap_string"
echo "$channel_spec_string"

#!/bin/bash
# ==========================================
# find_files_echo.sh
# Finds DMX files in a directory, preferring locally recorded files
# (underscore filenames) when near-complete.
# ==========================================

# --- Verbose logging helper ---
log() { [ "${VERBOSE_MODE:-0}" -eq 1 ] && echo "$@" >&2; }


day_dir="$1"

if [ -z "$day_dir" ]; then
    echo "Usage: $0 /path/to/day_dir" >&2
    exit 1
fi

if [ ! -d "$day_dir" ]; then
    echo "ERROR: Day directory not found: $day_dir" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="$SCRIPT_DIR/config.txt"

# --- Load config values ---
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
fi

IGNORE_STRINGS="${IGNORE_STRINGS:-trig|\\.ss|\\.xml}"
THRESHOLD_MISSING_FILES="${THRESHOLD_MISSING_FILES:-60}"
TOTAL_EXPECTED=1440

# --- Find all DMX files (ignoring patterns) ---
all_files=$(find "$day_dir" -maxdepth 1 -type f -name "*.dmx" | grep -vE "$IGNORE_STRINGS")

# --- Split groups ---
underscore_files=$(echo "$all_files" | grep "_" || true)
space_files=$(echo "$all_files" | grep " " || true)

underscore_count=$(echo "$underscore_files" | grep -c .)
space_count=$(echo "$space_files" | grep -c .)

# --- Decision logic ---
missing=$((TOTAL_EXPECTED - underscore_count))


if [ "$underscore_count" -ge $((TOTAL_EXPECTED - THRESHOLD_MISSING_FILES)) ]; then
    log "Using underscore files only ($underscore_count / $TOTAL_EXPECTED, missing $missing)"
    echo "$underscore_files"
else
    log "Using all files (underscore: $underscore_count, space: $space_count)"
    echo "$all_files"
fi

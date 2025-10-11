#!/bin/bash

# This script finds the primary station name in a day directory by filtering
# out ancillary files and counting the majority occurrence.

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Input directory is provided as the first argument
DAY_DIR="$1"
# Minimum expected file count (Corrected to 144)
MIN_COUNT=144 

# Ignore strings (ancillary files)
IGNORE_STRINGS_ECHO="trig|\.ss|\.xml|\.mseed."
IGNORE_STRINGS_GECKO="trig|\.dmx|\.suds|\.mseed."

# ==============================================================================
# MAIN LOGIC
# ==============================================================================

# 1. Validation
if [[ -z "$DAY_DIR" || ! -d "$DAY_DIR" ]]; then
    echo "Usage: $0 /path/to/day_directory" >&2
    echo "Error: Directory '$DAY_DIR' not found or is not a directory." >&2
    exit 1
fi

# 2. Determine Recorder Type and Define Patterns
# FIX: Use the correct script name and robustly capture output.
RECORDER_TYPE=$(./detect_recorder_type.sh "$DAY_DIR" 2>/dev/null | head -n 1 | tr -d '[:space:]')

if [[ "$RECORDER_TYPE" == "echo" ]]; then
    IGNORE_PATTERNS="$IGNORE_STRINGS_ECHO"
    EXTENSION_PATTERN="\.dmx"
elif [[ "$RECORDER_TYPE" == "gecko" ]]; then
    IGNORE_PATTERNS="$IGNORE_STRINGS_GECKO"
    EXTENSION_PATTERN="\.ms"
else
    echo "⚠️ Error: Could not reliably determine recorder type for $DAY_DIR. Skipping." >&2
    exit 1
fi

echo "--- Checking $DAY_DIR ---"
echo "Recorder Type: $RECORDER_TYPE"

# 3. Filter, Extract (using AWK), and Count Frequencies
STATION_COUNTS=$(
    # List files, filter ignores
    find "$DAY_DIR" -maxdepth 1 -type f -exec basename {} \; | 
    grep -vE "$IGNORE_PATTERNS" |
    
    # Extract Station Name using AWK (Robust against spaces/underscores)
    # This AWK command is the key to correctly getting 'ABM1Y' instead of 'Y'.
    awk -v ext="$EXTENSION_PATTERN" '
        BEGIN { FS=ext } 
        {
            # $1 contains the part before the extension (e.g., "...ABM1Y")
            # Split $1 by space/underscore and take the LAST field.
            n=split($1, a, /[[:space:]_]+/)
            print a[n] 
        }
    ' |
    
    # Final Filtering and Counting
    grep -E '^[A-Z0-9]+$' | 
    sort | 
    uniq -c | 
    sort -nr
)

# 4. Validate and Report
if [[ -z "$STATION_COUNTS" ]]; then
    echo "❌ No valid primary data files found after filtering/extraction."
    exit 0
fi

MAJORITY_LINE=$(echo "$STATION_COUNTS" | head -n 1)
MAJORITY_COUNT=$(echo "$MAJORITY_LINE" | awk '{print $1}')
MAJORITY_STATION=$(echo "$MAJORITY_LINE" | awk '{print $2}')
UNIQUE_STATIONS=$(echo "$STATION_COUNTS" | wc -l)

echo "Primary Station: ${MAJORITY_STATION} (Count: ${MAJORITY_COUNT})"

if [[ "$MAJORITY_COUNT" -ge "$MIN_COUNT" ]]; then
    echo "✅ Count OK (${MIN_COUNT}+ satisfied). Data integrity appears good."
else
    echo "⚠️ Count LOW (${MAJORITY_COUNT} < ${MIN_COUNT}). Data is below the expected minimum presence."
fi

if [[ "$UNIQUE_STATIONS" -gt 1 ]]; then
    echo "Found ${UNIQUE_STATIONS} total station names. Errands present:"
    echo "$STATION_COUNTS" | tail -n +2 | sed 's/^ *//'
fi

exit 0

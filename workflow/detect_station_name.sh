#!/bin/bash

# Configuration (Only the core required data)
DAY_DIR="$1"
IGNORE_STRINGS_ECHO="trig|\.ss|\.xml|\.mseed."
IGNORE_STRINGS_GECKO="trig|\.dmx|\.suds|\.mseed."

# 1. Input Validation
if [[ -z "$DAY_DIR" || ! -d "$DAY_DIR" ]]; then
    # Output an empty string and exit quietly on failure for easy shell integration
    exit 1
fi

# 2. Determine Recorder Type and Define Patterns
# FIX: The EXTENSION_PATTERN variables are now defined WITHOUT the backslash 
# to prevent the AWK warning, as AWK treats FS literally when set via -v.
RECORDER_TYPE=$(./detect_recorder_type.sh "$DAY_DIR" 2>/dev/null | head -n 1 | tr -d '[:space:]')

if [[ "$RECORDER_TYPE" == "echo" ]]; then
    IGNORE_PATTERNS="$IGNORE_STRINGS_ECHO"
    EXTENSION_PATTERN=".dmx"  # Changed from "\.dmx"
elif [[ "$RECORDER_TYPE" == "gecko" ]]; then
    IGNORE_PATTERNS="$IGNORE_STRINGS_GECKO"
    EXTENSION_PATTERN=".ms"   # Changed from "\.ms"
else
    # Output empty string on unknown type
    exit 1
fi

# 3. Filter, Extract, and Output Majority Station Name
# All commands are piped together to minimize I/O and achieve speed.
find "$DAY_DIR" -maxdepth 1 -type f -exec basename {} \; | 
grep -vE "$IGNORE_PATTERNS" |

# Extract Station Name using AWK (Now warning-free)
awk -v ext="$EXTENSION_PATTERN" '
    BEGIN { FS=ext } 
    {
        # Split everything before the extension by space/underscore and take the last field
        n=split($1, a, /[[:space:]_]+/)
        print a[n] 
    }
' |

# Filter out non-alphanumeric junk, count unique occurrences, and sort by frequency
grep -E '^[A-Z0-9]+$' | 
sort | 
uniq -c | 
sort -nr |

# Output ONLY the station name from the single line with the highest count
awk 'NR==1 {print $2}'

exit 0

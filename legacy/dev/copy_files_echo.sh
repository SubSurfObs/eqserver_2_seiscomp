#!/bin/bash
# copy_files_echo.sh
# Usage: ./copy_files_echo.sh [-v] /path/to/input /path/to/output
# Copies underscored echo files using iterative timestamp counting

# Default verbose off
VERBOSE=0
if [[ "$1" == "-v" || "$1" == "--verbose" ]]; then
    VERBOSE=1
    shift
fi

in_dir="$1"
out_dir="$2"

if [[ -z "$in_dir" || ! -d "$in_dir" ]]; then
    echo "Usage: $0 [-v] /path/to/input /path/to/output"
    exit 1
fi

mkdir -p "$out_dir"

# List all underscored files
find "$in_dir" -maxdepth 1 -type f -name "*_*_*_*" | sort > /tmp/underscored_files.txt
total=$(wc -l < /tmp/underscored_files.txt)
[[ $VERBOSE -eq 1 ]] && echo "Directory: $in_dir"
[[ $VERBOSE -eq 1 ]] && echo "Total underscored files: $total"

# If exactly 1440, copy all
if [[ $total -eq 1440 ]]; then
    [[ $VERBOSE -eq 1 ]] && echo "Exactly 1440 files. Copying all to $out_dir"
    xargs -a /tmp/underscored_files.txt -I{} cp -v {} "$out_dir"
else
    remaining="/tmp/underscored_files.txt"
    THRESHOLD=10  # default threshold for iterative copy

    while [ -s "$remaining" ]; do
        # Find most frequent timestamp (3rd field in filename)
        ts=$(awk -F'_' '{count[$3]++} END {for (t in count) print count[t], t}' "$remaining" | sort -nr | head -1 | awk '{print $2}')
        
        # Count files with this timestamp
        count=$(awk -F'_' -v t="$ts" '$3==t' "$remaining" | wc -l)
        [[ $VERBOSE -eq 1 ]] && echo "Most common timestamp: $ts (count $count)"
        
        if [[ $count -ge $THRESHOLD ]]; then
            [[ $VERBOSE -eq 1 ]] && echo "Copying files for timestamp $ts"
            awk -F'_' -v t="$ts" '$3==t {print $0}' "$remaining" | xargs -I{} cp -v {} "$out_dir"
        fi
        
        # Remove these files from the remaining list
        awk -F'_' -v t="$ts" '$3!=t {print $0}' "$remaining" > "${remaining}.tmp"
        mv "${remaining}.tmp" "$remaining"
    done
fi

rm -f /tmp/underscored_files.txt

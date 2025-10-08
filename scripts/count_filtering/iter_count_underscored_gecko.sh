#!/bin/bash
# iter_count_underscored_gecko.sh
# Counts underscored files with exactly 2 underscores, excluding certain patterns.
# Usage: ./iter_count_underscored_gecko.sh /path/to/directory

dir="$1"

if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    echo "Usage: $0 /path/to/directory"
    exit 1
fi

echo "Directory: $dir"

# Get list of valid underscored files
files=$(ls "$dir" | awk -F'_' 'NF==3' | grep -v -iE 'trig|dmx|mseed\.zip|ss')

total_files=$(echo "$files" | wc -l)
echo "Total underscored files considered: $total_files"

# Count most common timestamp (3rd field)
most_common=$(echo "$files" | awk -F'_' '{print $3}' | sort | uniq -c | sort -nr | head -1)

if [ -n "$most_common" ]; then
    count=$(echo "$most_common" | awk '{print $1}')
    timestamp=$(echo "$most_common" | awk '{print $2}')
    echo "Most common timestamp: $timestamp"
    echo "Number of files matching this timestamp: $count"
else
    echo "No valid files found"
fi

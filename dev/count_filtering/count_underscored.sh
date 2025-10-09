#!/bin/bash
# count_underscored.sh
# Usage: ./count_underscored.sh /path/to/directory

# Check input
if [ -z "$1" ]; then
    echo "Usage: $0 /path/to/directory"
    exit 1
fi

DIR="$1"

if [ ! -d "$DIR" ]; then
    echo "ERROR: Directory not found: $DIR"
    exit 1
fi

# Find the most common timestamp among files with 3 underscores
most_common=$(ls "$DIR" | awk -F'_' 'NF==4 {print $3}' | sort | uniq -c | sort -nr | head -1 | awk '{print $2}')

if [ -z "$most_common" ]; then
    echo "No files with 3 underscores found in $DIR"
    exit 0
fi

# Count files with 3 underscores and that timestamp
count=$(ls "$DIR" | awk -F'_' -v ts="$most_common" 'NF==4 && $3==ts' | wc -l)

echo "Directory: $DIR"
echo "Most common timestamp: $most_common"
echo "Number of files with this timestamp and 3 underscores: $count"

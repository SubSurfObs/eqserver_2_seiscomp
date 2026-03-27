#!/bin/bash
# iter_count_underscored_fast.sh
# Usage: ./iter_count_underscored_fast.sh /path/to/directory

dir="$1"
if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    echo "Usage: $0 /path/to/directory"
    exit 1
fi

# List all underscored files
find "$dir" -maxdepth 1 -type f -name "*_*_*_*" | sort > /tmp/underscored_files.txt

total=$(wc -l < /tmp/underscored_files.txt)
echo "Directory: $dir"
echo "Total underscored files: $total"

remaining="/tmp/underscored_files.txt"

while [ -s "$remaining" ]; do
    # Find most frequent timestamp
    ts=$(awk -F'_' '{count[$3]++} END {for (t in count) print count[t], t}' "$remaining" | sort -nr | head -1 | awk '{print $2}')
    
    # Count files with this timestamp
    count=$(awk -F'_' -v t="$ts" '$3==t' "$remaining" | wc -l)
    echo "Most common timestamp: $ts"
    echo "Number of files matching this timestamp: $count"
    
    # Remove these files from the list
    awk -F'_' -v t="$ts" '$3!=t' "$remaining" > "${remaining}.tmp"
    mv "${remaining}.tmp" "$remaining"
done

rm -f "$remaining"

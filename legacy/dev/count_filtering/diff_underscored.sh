#!/bin/bash
# count_underscored_summary.sh
# Usage: ./count_underscored_summary.sh /path/to/dir

dir="$1"
if [ -z "$dir" ]; then
    echo "Usage: $0 /path/to/dir"
    exit 1
fi

# Total underscored files (3 underscores)
total=$(ls "$dir" | awk -F'_' 'NF==4' | wc -l)

# Most common timestamp
ts=$(ls "$dir" | awk -F'_' 'NF==4 {split($3,a,"."); print a[1]}' \
     | sort | uniq -c | sort -nr | head -1 | awk '{print $2}')

# Files matching the most common timestamp
matching=$(ls "$dir" | awk -F'_' -v ts="$ts" 'NF==4 {split($3,a,"."); if(a[1]==ts) print $0}' | wc -l)

# Files not matching the most common timestamp
not_matching=$((total - matching))

echo "Directory: $dir"
echo "Total underscored files: $total"
echo "Most common timestamp: $ts"
echo "Number of files matching this timestamp: $matching"
echo "Number of underscored files not matching this timestamp: $not_matching"

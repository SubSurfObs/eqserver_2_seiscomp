#!/bin/bash
# iter_count_spaced.sh
# Usage: ./iter_count_spaced.sh /path/to/directory

dir="$1"

if [ -z "$dir" ] || [ ! -d "$dir" ]; then
    echo "Usage: $0 /path/to/directory"
    exit 1
fi

echo "Directory: $dir"

# Build list of spaced files, exclude mseed/trig
spaced_files=()
while IFS= read -r f; do
    spaced_files+=("$f")
done < <(find "$dir" -maxdepth 1 -type f \
          -not -iname '*mseed*' -not -iname '*trig*' \
          -printf "%f\n" | grep -P '^\d{4}-\d{2}-\d{2} \d{4} \d{2} .*\.dmx$')

total=${#spaced_files[@]}
echo "Total files considered: $total"

# Copy array so we can remove counts iteratively
remaining_files=("${spaced_files[@]}")

while [ ${#remaining_files[@]} -gt 0 ]; do
    # Extract timestamps (third field)
    timestamps=()
    for f in "${remaining_files[@]}"; do
        ts=$(echo "$f" | awk '{print $3}')
        timestamps+=("$ts")
    done

    # Find most common timestamp
    most_common=$(printf "%s\n" "${timestamps[@]}" | sort | uniq -c | sort -nr | head -1 | awk '{print $2}')
    count=$(printf "%s\n" "${timestamps[@]}" | grep -c "^$most_common$")

    echo "Most common timestamp: $most_common"
    echo "Number of files matching this timestamp: $count"

    # Remove files with this timestamp for next iteration
    new_remaining=()
    for f in "${remaining_files[@]}"; do
        ts=$(echo "$f" | awk '{print $3}')
        if [ "$ts" != "$most_common" ]; then
            new_remaining+=("$f")
        fi
    done
    remaining_files=("${new_remaining[@]}")
done

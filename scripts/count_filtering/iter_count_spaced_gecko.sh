#!/bin/bash
# Iteratively count spaced Gecko files like:
# '2023-10-24 0326 00 ABM1Y.ms.zip'
# ignoring underscored and excluded files.

dir="$1"

if [ -z "$dir" ]; then
  echo "Usage: $0 <directory>"
  exit 1
fi

echo "Directory: $dir"

# Collect only spaced Gecko files:
files=$(ls "$dir" 2>/dev/null \
  | grep -v -iE 'trig|dmx|mseed\.zip|ss' \
  | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{4} [0-9]{2} [A-Z0-9_]+\.(ms|dmx)(\.zip)?$')

total=$(echo "$files" | grep -c .)
echo "Total spaced Gecko files considered: $total"

if [ "$total" -eq 0 ]; then
  echo "No matching spaced Gecko files found."
  exit 0
fi

# Iteratively extract and count unique seconds fields
while [ "$total" -gt 0 ]; do
  common=$(echo "$files" | awk '{print $3}' | sort | uniq -c | sort -nr | head -n 1 | awk '{print $2}')
  count=$(echo "$files" | awk -v sec="$common" '$3 == sec' | wc -l)

  echo "Most common seconds value: $common"
  echo "Number of files matching this seconds value: $count"

  # Remove those lines from the list for next iteration
  files=$(echo "$files" | awk -v sec="$common" '$3 != sec')
  total=$(echo "$files" | grep -c .)

  # Stop if no files remain
  [ "$total" -le 0 ] && break
done

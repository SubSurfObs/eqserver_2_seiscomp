#!/bin/bash
# process_day_gecko.sh
# Usage: ./process_day.sh /path/to/day_dir /path/to/sds_archive /path/to/temp_base
# Example: ./process_day.sh /data/repository/archive/ABM1Y/continuous/2023/10/29 sds_test_archive temp_processing

day_dir="$1"           # full path to the day directory
sds_archive="$2"       # SDS archive path
temp_base="$3"         # temp working directory

if [ -z "$day_dir" ] || [ -z "$sds_archive" ] || [ -z "$temp_base" ]; then
    echo "Usage: $0 /path/to/day_dir /path/to/sds_archive /path/to/temp_base"
    exit 1
fi

# Ensure day_dir exists
if [ ! -d "$day_dir" ]; then
    echo "ERROR: Day directory not found: $day_dir"
    exit 1
fi

# Determine script location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

mkdir -p "$temp_base"
mkdir -p "$sds_archive"

# Temporary directory for this day
temp_day_dir="$temp_base/$(basename "$day_dir")"
mkdir -p "$temp_day_dir"

# Copy only files (ignore subdirectories)
find "$day_dir" -maxdepth 1 -type f -exec cp {} "$temp_day_dir/" \;

# Run the copy_minute_files.sh script
"$SCRIPT_DIR/copy_minute_files.sh" "$day_dir" "$temp_day_dir"

# Unzip any remaining .zip files in place
for f in "$temp_day_dir"/*.zip; do
    [ -e "$f" ] || continue
    unzip -o -q "$f" -d "$temp_day_dir" && rm "$f"
done

# Concatenate all .ms files in sorted order (safe for spaces)
tmp_full=$(mktemp)
find "$temp_day_dir" -maxdepth 1 -type f -name "*.ms" -print0 | sort -z | xargs -0 cat > "$tmp_full"
mv "$tmp_full" "$temp_day_dir/full.ms"

# Sort and merge MiniSEED headers
scmssort -u -E "$temp_day_dir/full.ms" > "$temp_day_dir/sorted.mseed"

# Import into SDS archive, renaming network to VW
scart -I "$temp_day_dir/sorted.mseed" \
      --with-filecheck \
      --rename "VW.-.-.-" \
      "$sds_archive"

echo "Finished processing day: $(basename "$day_dir")"

# CLEANUP: remove temporary day directory
rm -rf "$temp_day_dir"

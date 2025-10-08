#!/bin/bash
# process_archive.sh
# Usage: ./process_archive.sh /path/to/archive /path/to/sds_archive /path/to/temp_base
# Example: ./process_archive.sh /data/repository/archive ABM1Y_sds temp_processing

archive_root="$1"      # e.g., /data/repository/archive or a single day folder
sds_archive="$2"       # e.g., sds_test_archive/
temp_base="$3"         # e.g., temp_processing

if [ -z "$archive_root" ] || [ -z "$sds_archive" ] || [ -z "$temp_base" ]; then
    echo "Usage: $0 /path/to/archive /path/to/sds_archive /path/to/temp_base"
    exit 1
fi

# Determine the directory where this script lives
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

mkdir -p "$temp_base"
mkdir -p "$sds_archive"

# Loop over each day directory: station_name/year/month/day
find "$archive_root" -mindepth 3 -maxdepth 3 -type d | sort | while read -r day_dir; do
    echo "Processing day: $day_dir"

    # Temporary directory for this day
    temp_day_dir="$temp_base/$(basename "$day_dir")"
    mkdir -p "$temp_day_dir"

    # Copy all files from the day's folder into temp_day_dir
    cp -r "$day_dir"/* "$temp_day_dir/"

    # Run the copy_minute_files_gecko.sh script (from same folder as this script)
    "$SCRIPT_DIR/copy_minute_files_gecko.sh" "$day_dir" "$temp_day_dir"

    # Unzip any remaining .zip files in place
    for f in "$temp_day_dir"/*.zip; do
        [ -e "$f" ] || continue
        unzip -o "$f" -d "$temp_day_dir" && rm "$f"
    done

    # Concatenate all .ms files in sorted order (safe for spaces)
    find "$temp_day_dir" -maxdepth 1 -type f -name "*.ms" -print0 | sort -z | xargs -0 cat > "$temp_day_dir/full.ms"

    # Sort and merge MiniSEED headers
    scmssort -u -E "$temp_day_dir/full.ms" > "$temp_day_dir/sorted.mseed"

    # Import into SDS archive, renaming network to VW
    scart -I "$temp_day_dir/sorted.mseed" \
          --with-filecheck \
          --rename "VW.-.-.-" \
          "$sds_archive"

    echo "Finished processing $(basename "$day_dir")"

    # CLEANUP: remove temporary day directory to save space
    rm -rf "$temp_day_dir"
done

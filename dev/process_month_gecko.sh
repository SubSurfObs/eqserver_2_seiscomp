#!/bin/bash
# Usage: ./process_month_gecko.sh /path/to/month_dir /path/to/sds_archive /path/to/temp_base
# Example: ./process_month_gecko.sh /data/repository/archive/ABM1Y/continuous/2023/10 sds_test_archive temp_processing

month_dir="$1"
sds_archive="$2"
temp_base="$3"

if [ -z "$month_dir" ] || [ -z "$sds_archive" ] || [ -z "$temp_base" ]; then
    echo "Usage: $0 /path/to/month_dir /path/to/sds_archive /path/to/temp_base"
    exit 1
fi

# Determine script location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Loop over day subdirectories
for day_sub in "$month_dir"/*; do
    [ -d "$day_sub" ] || continue  # Skip non-directories
    day_name=$(basename "$day_sub")
    
    # Only process directories that look like a day (numeric name)
    if [[ ! "$day_name" =~ ^[0-9]{1,2}$ ]]; then
        continue
    fi

    # Skip empty directories
    if ! find "$day_sub" -maxdepth 1 -type f | read -r; then
        echo "⚠️  Skipping empty day directory: $day_name"
        continue
    fi

    # Process the day
    "$SCRIPT_DIR/process_day_gecko.sh" "$day_sub" "$sds_archive" "$temp_base"
done

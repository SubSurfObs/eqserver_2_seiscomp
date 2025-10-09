#!/bin/bash
# Usage: ./process_year_gecko.sh /path/to/year_dir /path/to/sds_archive /path/to/temp_base
# Example: ./process_year_gecko.sh /data/repository/archive/ABM1Y/continuous/2023 sds_test_archive temp_processing

year_dir="$1"
sds_archive="$2"
temp_base="$3"

if [ -z "$year_dir" ] || [ -z "$sds_archive" ] || [ -z "$temp_base" ]; then
    echo "Usage: $0 /path/to/year_dir /path/to/sds_archive /path/to/temp_base"
    exit 1
fi

# Determine script location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Loop over month subdirectories
for month_sub in "$year_dir"/*; do
    [ -d "$month_sub" ] || continue  # Skip non-directories
    month_name=$(basename "$month_sub")
    
    # Only process directories that look like a month (1-2 digits)
    if [[ ! "$month_name" =~ ^[0-9]{1,2}$ ]]; then
        continue
    fi

    # Skip empty month directories
    if ! find "$month_sub" -maxdepth 1 -type d | grep -q .; then
        echo "⚠️  Skipping empty month directory: $month_name"
        continue
    fi

    # Call the month script
    "$SCRIPT_DIR/process_month_gecko.sh" "$month_sub" "$sds_archive" "$temp_base"
done

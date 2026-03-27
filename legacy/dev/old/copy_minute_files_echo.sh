#!/bin/bash
# Usage: ./copy_minute_files.sh input_dir output_dir

in_dir="$1"
out_dir="$2"

if [ -z "$in_dir" ] || [ -z "$out_dir" ]; then
    echo "Usage: $0 input_dir output_dir"
    exit 1
fi

# Ensure absolute path for output
out_dir=$(realpath "$out_dir")
mkdir -p "$out_dir"

# Loop over hours and minutes
for hh in $(seq -w 0 23); do
    for mm in $(seq -w 0 59); do
        hhmm="${hh}${mm}"

        # Check if any underscored files exist for this HHMM
        if find "$in_dir" -maxdepth 1 -type f -name "*_${hhmm}_*" ! -name "*.trig*" -print -quit | grep -q .; then
            # Copy all underscored files safely
            find "$in_dir" -maxdepth 1 -type f -name "*_${hhmm}_*" ! -name "*.trig*" -print0 2>/dev/null | \
            while IFS= read -r -d '' file; do
                cp "$file" "$out_dir/"
                echo "Copied underscored: $file"
            done
        else
            # Copy all spaced files safely if no underscored files exist
            find "$in_dir" -maxdepth 1 -type f -name "* $hhmm *" ! -name "*.trig*" -print0 2>/dev/null | \
            while IFS= read -r -d '' file; do
                cp "$file" "$out_dir/"
                echo "Copied spaced: $file"
            done
        fi

    done
done


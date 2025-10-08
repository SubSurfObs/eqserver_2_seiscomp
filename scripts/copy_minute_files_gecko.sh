#!/bin/bash

# Default verbose off
VERBOSE=0

# Check for verbose flag
if [[ "$1" == "-v" || "$1" == "--verbose" ]]; then
    VERBOSE=1
    shift
fi

in_dir="$1"
out_dir="$2"

mkdir -p "$out_dir"

# Keep track of which minutes already have an underscored file
declare -A underscored_exists

for file in "$in_dir"/*.ms*; do
    [ -e "$file" ] || continue
    base=$(basename "$file")

    # Extract HHMM from underscored (e.g., 20231029_0001_ABM1Y.ms) or spaced (2023-10-29 0001 00 ABM1Y.ms)
    if [[ "$base" =~ ([0-2][0-9][0-5][0-9]) ]]; then
        hhmm="${BASH_REMATCH[1]}"
    else
        [[ $VERBOSE -eq 1 ]] && echo "Cannot extract HHMM from $base"
        continue
    fi

    if [[ "$base" == *_*_* ]]; then
        # Underscored file: copy and mark HHMM
        cp "$file" "$out_dir/"
        underscored_exists["$hhmm"]=1
        [[ $VERBOSE -eq 1 ]] && echo "Copied underscored: $file"
    else
        # Spaced file: only copy if no underscored for this HHMM
        if [[ -z "${underscored_exists[$hhmm]}" ]]; then
            cp "$file" "$out_dir/"
            [[ $VERBOSE -eq 1 ]] && echo "Copied spaced: $file"
        fi
    fi
done

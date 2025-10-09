#!/bin/bash
# ==========================================
# detect_archive_level.sh
# Determines whether a given path represents a year, month, or day directory.
# ==========================================

input_dir="$1"

if [ -z "$input_dir" ]; then
  echo "Usage: $0 /path/to/archive" >&2
  exit 1
fi

if [ ! -d "$input_dir" ]; then
  echo "Error: $input_dir is not a directory" >&2
  exit 1
fi

# --- Normalize and get subdirectories ---
subdirs=$(find "$input_dir" -mindepth 1 -maxdepth 1 -type d | sort)
day_files=$(find "$input_dir" -maxdepth 1 -type f \( -name "*.dmx" -o -name "*.ms" -o -name "*.ms.zip" -o -name "*.dmx.gz" \) | wc -l)

# --- Check for data files (day level) ---
if [ "$day_files" -gt 0 ]; then
  echo "DAY"
  exit 0
fi

# --- Count two-digit subdirectories ---
two_digit_subdirs=$(echo "$subdirs" | grep -E '.*/[0-9]{2}$' | wc -l)

if [ "$two_digit_subdirs" -ge 28 ]; then
  echo "MONTH"
elif [ "$two_digit_subdirs" -gt 0 ] && [ "$two_digit_subdirs" -le 12 ]; then
  echo "YEAR"
else
  echo "UNKNOWN"
fi

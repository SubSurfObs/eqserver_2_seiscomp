#!/bin/bash
# detect_archive_level.sh
# Detects whether the given directory represents a YEAR, MONTH, or DAY archive level.

dir="$1"

# --- 1. DAY LEVEL ---
# If directory contains seismic data files directly
if find "$dir" -maxdepth 1 -type f \( \
     -name "*.ms" -o -name "*.ms.zip" -o \
     -name "*.dmx" -o -name "*.dmx.gz" -o \
     -name "*.suds" -o -name "*.suds.gz" \) | grep -q .; then
  echo "DAY"
  exit 0
fi

# --- 2. MONTH vs YEAR LEVEL ---
# Gather subdirectories
subdirs=($(find "$dir" -mindepth 1 -maxdepth 1 -type d))
num_subdirs=${#subdirs[@]}

# If no subdirs and no data files → UNKNOWN
if [ "$num_subdirs" -eq 0 ]; then
  echo "UNKNOWN"
  exit 0
fi

# Check if subdirs contain data files directly (indicates MONTH level)
for sub in "${subdirs[@]}"; do
  if find "$sub" -maxdepth 1 -type f \( \
       -name "*.ms" -o -name "*.ms.zip" -o \
       -name "*.dmx" -o -name "*.dmx.gz" -o \
       -name "*.suds" -o -name "*.suds.gz" \) | grep -q .; then
    echo "MONTH"
    exit 0
  fi
done

# If subdirs themselves contain only further subdirectories (no files)
echo "YEAR"
exit 0

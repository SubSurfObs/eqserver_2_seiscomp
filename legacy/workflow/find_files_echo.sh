#!/bin/bash
# ================================================================
# find_files_echo.sh
# Lists valid Echo files (*.dmx or *.dmx.gz) in a day directory.
# Prefers underscored files if nearly complete (threshold-based),
# but uses all spaced files if no underscored ones exist.
# ================================================================

set -euo pipefail

day_dir="${1:-}"
if [ -z "$day_dir" ]; then
  echo "Usage: $0 /path/to/day_dir" >&2
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="$SCRIPT_DIR/config.txt"
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

# ---------------------- Config defaults ----------------------
IGNORE_STRINGS_ECHO=${IGNORE_STRINGS_ECHO:-"trig|\\.ss|\\.xml|\\.mseed"}
THRESHOLD_MISSING_FILES=${THRESHOLD_MISSING_FILES:-60}
EXPECTED_FILES=1440

# ---------------------- Find candidate files ----------------------
all_files=$(find "$day_dir" -maxdepth 1 -type f \( -name "*.dmx" -o -name "*.dmx.gz" \) | grep -Ev "$IGNORE_STRINGS_ECHO" || true)
[ -z "$all_files" ] && exit 0

# ---------------------- Classify ----------------------
underscore_files=$(echo "$all_files" | grep -E '/[^ ]*_[^ ]*_[^ ]*\.dmx(\.gz)?$' || true)
space_files=$(echo "$all_files" | grep -E '/.* .*\.dmx(\.gz)?$' || true)

underscore_count=$(echo "$underscore_files" | grep -c . || true)
space_count=$(echo "$space_files" | grep -c . || true)

# ---------------------- Decision logic ----------------------
if [ "$underscore_count" -gt 0 ] && [ "$space_count" -gt 0 ]; then
  if [ "$underscore_count" -ge $((EXPECTED_FILES - THRESHOLD_MISSING_FILES)) ]; then
    echo "$underscore_files"
  else
    echo "$all_files"
  fi
elif [ "$underscore_count" -gt 0 ]; then
  echo "$underscore_files"
elif [ "$space_count" -gt 0 ]; then
  echo "$space_files"
else
  exit 0
fi

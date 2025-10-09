#!/bin/bash
# process_archive.sh
# Simplified unified workflow for echo and gecko recorders

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

input_dir="$1"
sds_archive="$2"

if [ -z "$input_dir" ] || [ -z "$sds_archive" ]; then
  echo "Usage: $0 /path/to/archive /path/to/sds_archive"
  exit 1
fi

# --- Detect archive level ---
level=$("$SCRIPT_DIR/detect_archive_level.sh" "$input_dir")
echo "Detected archive level: $level"

# --- Define recursive handler ---
process_day_dir() {
  local day_dir="$1"
  echo "Checking recorder type in $day_dir"
  rec_type=$("$SCRIPT_DIR/check_recorder_type.sh" "$day_dir")
  echo "Detected recorder type: $rec_type"

  case "$rec_type" in
    echo)
      "$SCRIPT_DIR/process_day_echo.sh" "$day_dir" "$sds_archive" /tmp
      ;;
    gecko)
      "$SCRIPT_DIR/process_day_gecko.sh" "$day_dir" "$sds_archive" /tmp
      ;;
    *)
      echo "Warning: Unknown recorder type for $day_dir, skipping"
      ;;
  esac
}

# --- Recurse depending on level ---
case "$level" in
  DAY)
    process_day_dir "$input_dir"
    ;;
  MONTH)
    for d in "$input_dir"/*/; do
      [ -d "$d" ] || continue
      process_day_dir "$d"
    done
    ;;
  YEAR)
    for m in "$input_dir"/*/; do
      [ -d "$m" ] || continue
      for d in "$m"/*/; do
        [ -d "$d" ] || continue
        process_day_dir "$d"
      done
    done
    ;;
  *)
    echo "Error: Unable to detect archive level or directory empty."
    exit 1
    ;;
esac

echo "✅ Finished processing: $input_dir"

#!/bin/bash
# Fast recorder type detector

day_dir="$1"
prev_type="$2"
threshold="$3"

if [ -z "$day_dir" ]; then
  echo "Usage: $0 /path/to/day_dir [previous_type] [threshold]" >&2
  exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="$SCRIPT_DIR/config.txt"
[ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

MIN_FILE_THRESHOLD="${threshold:-${MIN_FILE_THRESHOLD:-100}}"

# --- Single scan: count file types in one go ---
read dmx_count ms_count <<<$(find "$day_dir" -maxdepth 1 -type f \
  | awk -F/ '
      /(\.dmx(\.gz)?$)/   {dmx++}
      /(\.ms(\.zip)?$)/   {ms++}
      END {print dmx+0, ms+0}' )

total=$((dmx_count + ms_count))

log() { [ "${VERBOSE_MODE:-0}" -eq 1 ] && echo "$@" >&2; }
log "[$(basename "$day_dir")] Echo=$dmx_count Gecko=$ms_count Total=$total"

# --- Decision logic ---
if [ "$ms_count" -eq 0 ] && [ "$dmx_count" -gt 0 ]; then
  echo "echo"; exit 0
fi
if [ "$dmx_count" -eq 0 ] && [ "$ms_count" -gt 0 ]; then
  echo "gecko"; exit 0
fi

if [ "$total" -ge "$MIN_FILE_THRESHOLD" ]; then
  if [ "$dmx_count" -gt "$ms_count" ]; then
    echo "echo"
  else
    echo "gecko"
  fi
else
  echo "${prev_type:-echo}"
fi

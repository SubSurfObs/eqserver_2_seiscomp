#!/bin/bash
# report_echo_day.sh
# Usage: ./report_echo_day.sh /path/to/day_directory

day_dir="$1"

if [[ -z "$day_dir" || ! -d "$day_dir" ]]; then
    echo "Usage: $0 /path/to/day_directory"
    exit 1
fi

# Most frequent timestamp for underscored files, ignoring trig.dmx and .mseed*
ts_us=$(ls "$day_dir"/*_*_*_* 2>/dev/null | grep -vE 'trig\.dmx|\.mseed' | awk -F'_' '{print $3}' | sort | uniq -c | sort -nr | head -1)

# Most frequent timestamp for spaced files, ignoring trig.dmx and .mseed*
ts_sp=$(ls "$day_dir"/*\ *\ *\ * 2>/dev/null | grep -vE 'trig\.dmx|\.mseed' | awk '{print $3}' | sort | uniq -c | sort -nr | head -1)

echo "Day $day_dir: Underscored -> $ts_us ; Spaced -> $ts_sp"

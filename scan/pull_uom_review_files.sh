#!/bin/bash
# Pull source-archive sample files from staging VM to local Downloads for
# uom_seismic_metadata review (2026-06-11 round).
#
# Three test sets:
#   - gain=2 windows (KRAN, NARR, HODL, TYHS) — PC-SUDS .dmx + LT mseed
#   - LOYU location codes (D, 00, 02, S) — raw eqserver mseed per epoch
#
# Usage:
#   bash scan/pull_uom_review_files.sh
#
# Set VM_HOST env var to override default ssh target.
set -e

VM_HOST=${VM_HOST:-dsand@172.26.144.41}
DEST=${HOME}/Downloads/uom_review_2026-06-11
EQSERVER_ARCHIVE=/mnt/eqserver_archive/shared/data/repository/archive
LT_ARCHIVE=/mnt/seiscomp_archive

mkdir -p "$DEST"/{gain2_test/KRAN,gain2_test/NARR,gain2_test/HODL,gain2_test/TYHS,loyu_locations}

# Find one representative file in a date range, prefer day-1 of given month
# Args: STATION YYYY-MM-DD-start YYYY-MM-DD-end exts
# Looks under eqserver_archive
pull_eqserver_sample() {
  local STA=$1
  local START=$2
  local END=$3
  local DEST_DIR=$4
  local PATTERN=${5:-"*.dmx*"}

  # ask the VM to find one matching file in the window
  local REMOTE_FILE
  REMOTE_FILE=$(ssh "$VM_HOST" "
    python3 << PYEOF
import datetime, os, glob
start = datetime.date.fromisoformat('$START')
end = datetime.date.fromisoformat('$END')
arch = '$EQSERVER_ARCHIVE/$STA/continuous'
d = start
while d <= end:
    p = os.path.join(arch, str(d.year), f'{d.month:02d}', f'{d.day:02d}')
    if os.path.isdir(p):
        for f in sorted(glob.glob(os.path.join(p, '$PATTERN'))):
            if '.trig' in f: continue
            print(f)
            raise SystemExit(0)
    d += datetime.timedelta(days=1)
PYEOF
" 2>/dev/null | head -1)

  if [ -z "$REMOTE_FILE" ]; then
    echo "  ! no file found for $STA $START..$END pattern=$PATTERN"
    return 1
  fi
  echo "  $STA $START..$END -> $(basename "$REMOTE_FILE")"
  scp -q "$VM_HOST:$REMOTE_FILE" "$DEST_DIR/"
}

# Same but pulls one LT-archive mseed file too (post-conversion, for cross-check)
pull_lt_sample() {
  local STA=$1
  local START=$2
  local DEST_DIR=$3
  local YEAR=${START:0:4}
  local DOY
  DOY=$(date -j -f "%Y-%m-%d" "$START" "+%j" 2>/dev/null || \
        ssh "$VM_HOST" "date -d $START +%j")

  local REMOTE_FILE
  REMOTE_FILE=$(ssh "$VM_HOST" "
    for cha in CHZ HHZ; do
      f=\$(ls $LT_ARCHIVE/$YEAR/VW/$STA/\${cha}.D/VW.$STA.*.\${cha}.D.$YEAR.$(printf '%03d' $((10#$DOY))) 2>/dev/null | head -1)
      [ -n \"\$f\" ] && echo \"\$f\" && break
    done
  " 2>/dev/null | head -1)

  if [ -z "$REMOTE_FILE" ]; then
    echo "  ! no LT mseed for $STA $START"
    return 1
  fi
  echo "  $STA LT mseed -> $(basename "$REMOTE_FILE")"
  scp -q "$VM_HOST:$REMOTE_FILE" "$DEST_DIR/"
}

echo "=== gain=2 test set (4 stations, 4 windows) ==="
echo "  Source bytes are .dmx (PC-SUDS, EchoPro era); LT bytes are post-conversion mseed."
echo "  WAVES/SUDS-Pi can read both."
echo ""

echo "[1/4] KRAN 2012-07 to 2012-10"
pull_eqserver_sample "KRAN" "2012-07-01" "2012-09-30" "$DEST/gain2_test/KRAN"
pull_lt_sample       "KRAN" "2012-08-01"                "$DEST/gain2_test/KRAN" || true

echo "[2/4] NARR 2012-06 to 2012-08"
pull_eqserver_sample "NARR" "2012-06-01" "2012-07-31" "$DEST/gain2_test/NARR"
pull_lt_sample       "NARR" "2012-07-01"                "$DEST/gain2_test/NARR" || true

echo "[3/4] HODL 2012-08 to 2012-09"
pull_eqserver_sample "HODL" "2012-08-01" "2012-08-31" "$DEST/gain2_test/HODL"
pull_lt_sample       "HODL" "2012-08-15"                "$DEST/gain2_test/HODL" || true

echo "[4/4] TYHS 2024-01 to 2024-02"
pull_eqserver_sample "TYHS" "2024-01-01" "2024-01-31" "$DEST/gain2_test/TYHS"
pull_lt_sample       "TYHS" "2024-01-15"                "$DEST/gain2_test/TYHS" || true

echo ""
echo "=== LOYU location-code test set (6 epochs) ==="
echo "  Raw eqserver mseed (.ms.zip) per epoch — verify the loc field in the bytes."
echo ""

echo "[1/6] LOYU 2014-01 to 2015-05 (loc=D)"
pull_eqserver_sample "LOYU" "2014-06-01" "2014-12-31" "$DEST/loyu_locations" "*.ms.zip"

echo "[2/6] LOYU 2016-05-01 to 2016-05-22 (loc=00)"
pull_eqserver_sample "LOYU" "2016-05-10" "2016-05-21" "$DEST/loyu_locations" "*.ms.zip"

echo "[3/6] LOYU 2016-05-22 to 2018-04-23 (loc=D)"
pull_eqserver_sample "LOYU" "2017-06-01" "2017-12-31" "$DEST/loyu_locations" "*.ms.zip"

echo "[4/6] LOYU 2018-04-23 to 2018-05-04 (loc=02)"
pull_eqserver_sample "LOYU" "2018-04-23" "2018-05-03" "$DEST/loyu_locations" "*.ms.zip"

echo "[5/6] LOYU 2018-05-04 to 2019-05-07 (loc=D)"
pull_eqserver_sample "LOYU" "2018-08-01" "2018-12-31" "$DEST/loyu_locations" "*.ms.zip"

echo "[6/6] LOYU 2019-05-07 onwards (loc=S)"
pull_eqserver_sample "LOYU" "2019-06-01" "2019-12-31" "$DEST/loyu_locations" "*.ms.zip"

# Rename LOYU files to surface the loc code claim
echo ""
echo "Annotating LOYU filenames with empirical loc claim..."
cd "$DEST/loyu_locations" 2>/dev/null && {
  i=0
  declare -a LABELS=(loc-D loc-00 loc-D loc-02 loc-D loc-S)
  for f in $(ls -t | head -6 | tac); do
    i=$((i+1))
    mv "$f" "${LABELS[$((i-1))]}__$f" 2>/dev/null || true
  done
} || true

# README
cat > "$DEST/README.md" << EOF
# uom_seismic_metadata review files — 2026-06-11

Pulled from eqserver staging VM ($VM_HOST) by
\`eqserver_2_seiscomp/scan/pull_uom_review_files.sh\`.

## gain=2 test set

Empirical scan surfaced \`gain: 2\` at four stations. Catalogue only
models gain 1/8/32. Need to confirm whether 2 is a real preamp
configuration or a reader artifact.

| Station | Window | Files |
|---|---|---|
| KRAN | 2012-07-01 → 2012-10-01 | gain2_test/KRAN/ |
| NARR | 2012-06-01 → 2012-08-01 | gain2_test/NARR/ |
| HODL | 2012-08-01 → 2012-09-01 | gain2_test/HODL/ |
| TYHS | 2024-01-01 → 2024-02-01 | gain2_test/TYHS/ |

Each subdir has one source-archive file (.dmx, PC-SUDS) plus one
LT-archive miniSEED file (post-conversion). WAVES / SUDS-Pi reads both.

## LOYU location-code test set

Empirical sees four distinct location codes at LOYU across six time
windows: D, 00, D, 02, D, S. User suspects D/S = deep/shallow naming
intent but isn't sure letter codes are SEED-valid.

Files in loyu_locations/, filename prefix = empirical loc claim. All
files are raw eqserver \`.ms.zip\`; verify the loc field in the
miniSEED blockette header.

## What to look for

**gain=2**: Is gain really 2 in the headers, or did atod_gain leak the
-32767 / 0 sentinel through? Compare to a known-gain-1 day on the
same station.

**LOYU location codes**: Open the mseed in WAVES / WAV-Pi, inspect the
location code field. Are D/S real bytes or reader artifacts? If real,
what does the transition pattern (D ↔ 00 ↔ D ↔ 02 ↔ D ↔ S) imply
operationally?
EOF

echo ""
echo "=== Done ==="
echo "Files in: $DEST"
du -sh "$DEST"
ls "$DEST"

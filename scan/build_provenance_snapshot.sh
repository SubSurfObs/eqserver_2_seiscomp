#!/bin/bash
# Build a per-network provenance snapshot under the staging mediaflux
# share. One snapshot per network per sweep — VW first, then VX, DU.
#
# Layout:
#   /mnt/seiscomp_staging/eqserver_provenance/<NETWORK>/<YYYY-MM-DD>/
#     jsonl/            (orchestrator state)
#     plans/            (per-station plan YAMLs)
#     run_manifests/    (per-station-year provenance)
#     convert_logs/     (per-station-year phase3 stdout)
#     recovery_logs/    (recovery script outputs, if any)
#     handoffs/         (cross-project handoff threads)
#     metadata/         (level2 metadata YAML if available)
#     recovery_register.yaml
#     code_sha.txt      (Git SHAs at snapshot time)
#     station_db_manifest.csv
#     station_dbs/      (60 GB of Level-1 SQLite files; rsync'd in background)
#     README.md
#
# Usage:
#   bash scan/build_provenance_snapshot.sh <NETWORK>
#
# Example:
#   bash scan/build_provenance_snapshot.sh VW
#
# Side effects: background rsync of station_dbs (60 GB over CIFS;
# 1-2 hours). Monitor via the printed rsync log path.

set -e

NETWORK=${1:?usage: $0 <NETWORK>}
DATE=$(date -u +%Y-%m-%d)
ROOT=/mnt/seiscomp_staging/eqserver_provenance/${NETWORK}/${DATE}
EQSERVER_REPO=${EQSERVER_REPO:-$HOME/projects/SubSurfObs/eqserver_2_seiscomp}
DISK_TO_SDS_REPO=${DISK_TO_SDS_REPO:-$HOME/projects/SubSurfObs/disk_to_sds}
STATION_DBS_DIR=${STATION_DBS_DIR:-$HOME/station_dbs}
SWEEP_DIR=${SWEEP_DIR:-/mnt/seiscomp_staging/eqserver_sweep}
CONVERT_LOGS_DIR=${CONVERT_LOGS_DIR:-/var/tmp/eqserver_sweep_convert_logs}
RECOVERY_LOGS_DIR=${RECOVERY_LOGS_DIR:-/var/tmp/eqserver_recovery_logs}

echo "=== Building provenance snapshot at $ROOT ==="
mkdir -p "$ROOT"/{jsonl,plans,run_manifests,convert_logs,recovery_logs,handoffs,metadata,station_dbs}

echo "  jsonl ..."
cp "$SWEEP_DIR"/*.jsonl "$ROOT"/jsonl/

echo "  plans (network=$NETWORK) ..."
cp "$SWEEP_DIR"/plans/${NETWORK}/*.plan.yaml "$ROOT"/plans/ 2>/dev/null || true

echo "  run_manifests ($(ls "$SWEEP_DIR"/run_manifests/ 2>/dev/null | wc -l) files) ..."
cp "$SWEEP_DIR"/run_manifests/*.json "$ROOT"/run_manifests/ 2>/dev/null || true

echo "  convert_logs (~19 MB) ..."
cp "$CONVERT_LOGS_DIR"/*.log "$ROOT"/convert_logs/ 2>/dev/null || true

echo "  recovery_logs ..."
cp "$RECOVERY_LOGS_DIR"/*.log "$ROOT"/recovery_logs/ 2>/dev/null || true

echo "  recovery_register.yaml ..."
cp "$EQSERVER_REPO"/docs/recovery_register.yaml "$ROOT"/ 2>/dev/null || true

echo "  handoffs/ ..."
cp -r "$EQSERVER_REPO"/handoffs/* "$ROOT"/handoffs/ 2>/dev/null || true

# Level-2 metadata YAML (if it exists for this network)
META_YAML=/tmp/${NETWORK,,}_observations_full.yaml
META_YAML_ALT=/tmp/vw_observations_full.yaml   # current naming
if [ "$NETWORK" = "VW" ] && [ -f "$META_YAML_ALT" ]; then
  echo "  metadata YAML ..."
  cp "$META_YAML_ALT" "$ROOT"/metadata/${NETWORK,,}_observations.yaml
elif [ -f "$META_YAML" ]; then
  echo "  metadata YAML ..."
  cp "$META_YAML" "$ROOT"/metadata/${NETWORK,,}_observations.yaml
else
  echo "  metadata YAML — not present yet; expected at $META_YAML_ALT or $META_YAML when level2_metadata_scan.py has been run"
fi

echo "  station_db_manifest.csv ..."
{
  echo "station,size_bytes,size_mb,mtime"
  for f in "$STATION_DBS_DIR"/${NETWORK}.*.db; do
    [ -f "$f" ] || continue
    NAME=$(basename "$f" .db)
    SIZE=$(stat -c %s "$f")
    SIZE_MB=$(( SIZE / 1024 / 1024 ))
    MTIME=$(stat -c %y "$f" | cut -d. -f1)
    echo "$NAME,$SIZE,$SIZE_MB,$MTIME"
  done
} > "$ROOT"/station_db_manifest.csv

echo "  code_sha.txt ..."
{
  echo "Snapshot generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Host: $(hostname)"
  echo "Network: $NETWORK"
  echo ""
  echo "eqserver_2_seiscomp HEAD: $(cd "$EQSERVER_REPO" && git rev-parse HEAD)"
  echo "eqserver_2_seiscomp short: $(cd "$EQSERVER_REPO" && git rev-parse --short HEAD)"
  echo "eqserver_2_seiscomp branch: $(cd "$EQSERVER_REPO" && git rev-parse --abbrev-ref HEAD)"
  echo ""
  echo "disk_to_sds HEAD: $(cd "$DISK_TO_SDS_REPO" && git rev-parse HEAD)"
  echo "disk_to_sds short: $(cd "$DISK_TO_SDS_REPO" && git rev-parse --short HEAD)"
} > "$ROOT"/code_sha.txt

# Generate README.md
DATE_HEADER=$(date -u +%Y-%m-%d)
cat > "$ROOT"/README.md << EOF
# $NETWORK network sweep provenance snapshot

**Date**: $DATE_HEADER
**Network**: $NETWORK

Captures the state of the $NETWORK conversion sweep at completion.
What got converted, from what source state, by which code, and the
trail of decisions (convert/promote/held) at each unit.

## Subdirectories

| Path | What |
|---|---|
| jsonl/ | convert_done, promote_done, held, convert_failed, cleanup_done |
| plans/ | Per-station plan YAMLs |
| run_manifests/ | One JSON per (sta, year) run with policy_sha + project_git + per-day status |
| convert_logs/ | Per-station-year phase3 stdout |
| recovery_logs/ | Logs from run_recovery_register.py |
| recovery_register.yaml | Recovery doc |
| handoffs/ | Cross-project handoff threads |
| metadata/ | level2_metadata_scan.py YAML (waveform-db source for the metadata project) |
| station_db_manifest.csv | Audit listing of Level-1 station DBs |
| station_dbs/ | Level-1 SQLite DBs (rsync'd separately, ~60 GB for VW) |
| code_sha.txt | Git SHAs at snapshot time |

## How to use this snapshot

- Audit a (sta, year): \`run_manifests/eqserver_${NETWORK}_<STA>_<YEAR>_*.json\` + the corresponding \`convert_logs/<run_id>.log\`.
- Re-derive a station's metadata: use \`station_dbs/${NETWORK}.<STA>.db\` + \`plans/${NETWORK}.<STA>.plan.yaml\`.
- Re-run a recovery: \`recovery_register.yaml\` + \`recovery_logs/\` document each entry's path.
- Reproduce the engine state: pin \`disk_to_sds\` to the SHA in \`code_sha.txt\`.

## Conventions

This snapshot follows the convention documented in
\`eqserver_2_seiscomp/CLAUDE.md\` § "Per-network sweep completion".
EOF

# Spot summary
echo ""
echo "=== Snapshot small-file copy complete ==="
du -sh "$ROOT"
echo ""
echo "Contents:"
ls -la "$ROOT"

# Background rsync of station DBs (60 GB for VW; size will vary per network)
echo ""
echo "=== Launching station_dbs rsync in background ==="
nohup rsync -av --partial \
  "$STATION_DBS_DIR"/${NETWORK}.*.db \
  "$ROOT"/station_dbs/ \
  > "$ROOT"/station_dbs/_rsync.log 2>&1 &
RSYNC_PID=$!
echo "rsync PID: $RSYNC_PID"
echo "tail with: tail -f $ROOT/station_dbs/_rsync.log"
echo ""
echo "Snapshot small files complete; station_dbs continuing in background."

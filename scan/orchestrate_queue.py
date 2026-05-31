"""Shared queue helpers for the eqserver production orchestrator.

The orchestrator is three scripts communicating via append-only JSONL queue
files on the shared staging mount:

    /mnt/seiscomp_staging/eqserver_sweep/
    ├── convert_done.jsonl   staging VM writes after phase3 finishes a (sta, year)
    ├── promote_done.jsonl   dev1 writes after apply.py --commit succeeds
    ├── held.jsonl           dev1 writes when apply.py --mode decide finds overrides > 0
    └── cleanup_done.jsonl   staging VM writes after staged copy verified deleted

Single-writer-per-file (disk_to_sds reply 03, 2026-05-31): CIFS cross-host
append is NOT atomic, so each file has exactly one writer host:

| File              | Writer        | Readers                       |
|-------------------|---------------|-------------------------------|
| convert_done.jsonl| staging VM    | dev1 (promote), VM (cleanup)  |
| promote_done.jsonl| dev1          | staging VM (cleanup)          |
| held.jsonl        | dev1          | operator (no automation)      |
| cleanup_done.jsonl| staging VM    | operator                      |

State = join by `run_id` across the four files. No SSH between hosts —
both hosts mount the staging CIFS share read-write, so coordination is
purely via shared filesystem.

Event schema (per line):

    {
      "run_id":   "eqserver_VW_<STA>_<YEAR>_<TS>",
      "net":      "VW",
      "sta":      "<STA>",
      "year":     2024,
      "run_manifest_path": "/tmp/eqserver_runs/VW_<STA>_<YEAR>.json",
      "staging_root":      "/mnt/seiscomp_staging/seiscomp_archive",
      "ts":       "<ISO 8601 UTC>",
      "action":   "converted" | "promoted" | "held" | "cleaned",
      ...kind-specific fields...
    }

Resume model: each script reads its OUTGOING queue file at startup, builds
a set of already-processed run_ids, and skips them. Append-only + idempotent
processing means restart-after-crash is safe.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Iterator


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def queue_dir(staging_root: Path) -> Path:
    """Default queue directory under the staging mount.

    Per disk_to_sds reply 03 (2026-05-31): use a dedicated subdir, NOT the
    staging SDS root, so the SDS skeleton stays clean. Callers typically pass
    the parent of the staging SDS (e.g. `/mnt/seiscomp_staging`), so this
    helper resolves to `/mnt/seiscomp_staging/eqserver_sweep/`.
    """
    return Path(staging_root) / "eqserver_sweep"


def append_event(queue_path: Path, event: dict) -> None:
    """Append one event line to the queue file. Creates parent dir if needed.

    Uses a simple write — the staging CIFS mount is rw and we have one writer
    per queue file (convert.py owns pending.jsonl, promote.py owns
    promoted.jsonl, cleanup.py owns cleaned.jsonl). No locking needed.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":"), sort_keys=True)
    with queue_path.open("a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_all_events(queue_path: Path) -> list[dict]:
    """Read every event in a queue file. Returns [] if file is missing."""
    if not queue_path.exists():
        return []
    events = []
    with queue_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # Tolerate a partial trailing line (writer was mid-flush).
                continue
    return events


def already_processed_ids(queue_path: Path) -> set[str]:
    """run_ids already in this queue file (resume helper)."""
    return {e["run_id"] for e in read_all_events(queue_path) if "run_id" in e}


def tail_events(queue_path: Path, last_offset: int) -> Iterator[tuple[int, dict]]:
    """Yield (new_offset, event) for events after `last_offset` bytes.

    Watcher pattern: the script keeps a running byte offset, calls this on each
    poll, and updates the offset from the last yielded tuple. Cheap on CIFS
    (just a small read past the cached size).
    """
    if not queue_path.exists():
        return
    size = queue_path.stat().st_size
    if size <= last_offset:
        return
    with queue_path.open() as f:
        f.seek(last_offset)
        # Read line-by-line; track bytes consumed precisely so partial trailing
        # lines don't get double-counted when the writer finishes flushing.
        consumed = last_offset
        for line in f:
            line_bytes = len(line.encode("utf-8"))
            if not line.endswith("\n"):
                # Partial trailing line — stop here; we'll pick it up next poll.
                break
            stripped = line.strip()
            consumed += line_bytes
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            yield consumed, event


def build_run_id(net: str, sta: str, year: int) -> str:
    """Canonical run_id format for orchestrated production runs.

    Includes a timestamp so re-runs of the same (sta, year) — e.g. after a
    classifier change — produce a distinct run_id and a fresh runs/<run_id>/
    record in the ledger.
    """
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"eqserver_{net}_{sta}_{year:04d}_{ts}"

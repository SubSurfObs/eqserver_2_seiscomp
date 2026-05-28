"""Cross-source file selection for Phase 3 conversion.

Pure-function policy module. Given the manifest rows for one station-day
(both disk AND telemetry candidates), returns the subset of files to feed
into the converter — without ever opening or reading any of the files.

Policy summary (filename + size signals only; no I/O):
    1. Per minute (HHMM) slot, one candidate -> use it.
    2. If a "complete" file (no channel_suffix) is present in the slot,
       use it. **Disk preferred unless disk is materially smaller than
       tele** (controlled by `disk_size_floor_ratio`, default 0.8 —
       disk wins down to 80% of tele's size). Single-channel stubs at
       this slot are dropped (the complete file covers their channel).
    3. If only single-channel stubs are present in the slot, keep one PER
       distinct channel_suffix (no overlap — they are different components).
       Within same-channel candidates: disk wins ABSOLUTELY (no threshold
       in tier 3; per-channel sizes are too noisy at ~5-15 KB to trust the
       ratio signal).

Why "complete" = "no channel_suffix":
    Per-channel files are named like `..._CHZ.mseed.zip`, `..._CHN.mseed.zip`.
    Full-station files have no channel suffix (`STBK.ms.zip`, `OUTU.dmx`).
    This holds for EchoPro, Gecko (.ms.zip), Minimus (per-channel mseed),
    PiesMo (HHZ-only stubs), and Reftek RT130. So one rule covers all
    recorders without needing recorder-specific branches.

Why the threshold on tier 2:
    Disk is the operator's intended record-keeping path, so we default to
    it. But when disk drops materially below tele (e.g. disk recorder cut
    out mid-minute and captured 10s while tele streamed the full 60s),
    "use disk anyway" would discard real data. The threshold lets tele win
    only when it has meaningfully more bytes. At 0.8 (default), disk wins
    for any disk_size >= 0.8 * tele_size (handles compression noise + minor
    overhead) and tele wins below that (the recorder-cut-out case).

The function is recorder-agnostic on purpose.
"""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable, List

DEFAULT_DISK_SIZE_FLOOR_RATIO = 0.8


def select_files_for_day(
    rows: Iterable[tuple],
    disk_size_floor_ratio: float = DEFAULT_DISK_SIZE_FLOOR_RATIO,
) -> List[str]:
    """Select the file paths to convert for one station-day.

    Parameters
    ----------
    rows : iterable of (path, source_type, hhmm, channel_suffix, size_bytes)
        Manifest rows for one (station, dir_year, dir_month, dir_day),
        with exclude_reason already filtered out. `source_type` is
        'disk' | 'telemetry'; `channel_suffix` is e.g. 'Z'/'N'/'E' for
        stubs, or None for complete files; `size_bytes` is from stat().
    disk_size_floor_ratio : float, default 0.8
        Tier 2 threshold. Disk wins iff
            disk_size >= disk_size_floor_ratio * tele_size
        Otherwise tele wins (because disk has materially less data).
        Set 1.0 for "disk wins only if at least as big as tele".
        Set 0.0 for "disk always wins regardless of size" (old policy).
        Does NOT apply to tier 1 (single candidate) or tier 3 (stubs).

    Returns
    -------
    list of paths to convert. Deterministic order: sorted by HHMM then path.

    Notes
    -----
    Pure function — no file I/O, no side effects. Decisions use ONLY the
    five passed-in fields. To change policy, change this function (and its
    tests in scan/test_cross_source.py); the rest of phase3 is untouched.
    """
    by_slot = defaultdict(list)
    for r in rows:
        path, source_type, hhmm, channel_suffix, size_bytes = r
        by_slot[hhmm].append({
            "path": path,
            "source_type": source_type,
            "channel_suffix": channel_suffix,
            "size_bytes": size_bytes or 0,
        })

    selected = []
    for hhmm in sorted(by_slot):
        cands = by_slot[hhmm]

        # Tier 1: single candidate → trivial use.
        if len(cands) == 1:
            selected.append(cands[0]["path"])
            continue

        # Tier 2: "complete" file (no channel_suffix) present → it covers all
        # channels; single-channel stubs at this slot are redundant.
        complete = [c for c in cands if c["channel_suffix"] is None]
        if complete:
            selected.append(_pick_complete(complete, disk_size_floor_ratio))
            continue

        # Tier 3: only single-channel stubs at this slot. Keep one per
        # distinct channel_suffix (they cover DIFFERENT components, no overlap).
        # Within a same-channel group, disk wins absolutely (no threshold).
        per_chan = defaultdict(list)
        for c in cands:
            per_chan[c["channel_suffix"]].append(c)
        for cs in sorted(per_chan):
            selected.append(_pick_stub(per_chan[cs]))

    return selected


def _pick_complete(cands: list, disk_size_floor_ratio: float) -> str:
    """Tier-2 picker: complete files at the same HHMM.
    Applies the disk-size-floor-ratio rule.

    If both disk and tele candidates exist:
      - largest disk vs largest tele
      - if largest_disk >= ratio * largest_tele: take largest_disk
      - else: take largest_tele (tele has materially more data)
    If only one source kind exists, take the largest within it.
    """
    disks = [c for c in cands if c["source_type"] == "disk"]
    teles = [c for c in cands if c["source_type"] == "telemetry"]
    if disks and not teles:
        return _largest(disks)
    if teles and not disks:
        return _largest(teles)
    # Both sides present — apply the threshold.
    best_disk = max(disks, key=lambda c: c["size_bytes"])
    best_tele = max(teles, key=lambda c: c["size_bytes"])
    if best_disk["size_bytes"] >= disk_size_floor_ratio * best_tele["size_bytes"]:
        return best_disk["path"]
    return best_tele["path"]


def _pick_stub(cands: list) -> str:
    """Tier-3 picker: single-channel stubs sharing the same channel_suffix.
    Disk wins absolutely; size is only a tiebreaker within a single source.
    No threshold here — per-channel sizes are too small (~5-15 KB) for
    the ratio signal to be reliable."""
    cands_sorted = sorted(
        cands,
        key=lambda c: (
            0 if c["source_type"] == "disk" else 1,   # disk first, always
            -(c["size_bytes"] or 0),                  # then larger
            c["path"],                                # then path
        ),
    )
    return cands_sorted[0]["path"]


def _largest(cands: list) -> str:
    """Deterministic 'pick the largest' for a homogeneous group."""
    cands_sorted = sorted(cands, key=lambda c: (-(c["size_bytes"] or 0), c["path"]))
    return cands_sorted[0]["path"]

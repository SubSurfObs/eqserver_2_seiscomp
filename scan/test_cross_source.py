#!/usr/bin/env python3
"""Unit tests for cross_source.select_files_for_day.

Each test names a real-world scenario from the EqServer archive and asserts
the policy from CLAUDE.md "Cross-source selection" comes out the way it should.
Run with:  python3 scan/test_cross_source.py
"""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from cross_source import select_files_for_day  # noqa: E402


def row(path, source, hhmm, suffix=None, size=10000):
    """Build a manifest-shaped row tuple matching the function's contract."""
    return (path, source, hhmm, suffix, size)


# ---------- Tier 1: single-candidate per slot ----------

def test_only_disk_present():
    """OUTU 2020-01-20 normal day — disk only, no tele at all."""
    rows = [row("disk/0001_OUTU.dmx", "disk", "0001")]
    assert select_files_for_day(rows) == ["disk/0001_OUTU.dmx"]


def test_only_tele_present():
    """USB pending upload — disk absent, tele covers the slot."""
    rows = [row("tele/0001_OUTU.dmx", "telemetry", "0001")]
    assert select_files_for_day(rows) == ["tele/0001_OUTU.dmx"]


# ---------- Tier 2: complete file beats stub ----------

def test_disk_complete_beats_tele_complete_equal_size():
    """EchoPro day: both disk and tele wrote the same minute, near-equal size.
    Disk wins under the default 0.8 threshold (disk_size >= 0.8 * tele_size)."""
    rows = [
        row("disk/0001_OUTU.dmx", "disk", "0001", None, 50000),
        row("tele/0001_OUTU.dmx", "telemetry", "0001", None, 49000),
    ]
    assert select_files_for_day(rows) == ["disk/0001_OUTU.dmx"]


def test_disk_wins_within_threshold_floor():
    """Disk is 80% of tele size — exactly at the 0.8 threshold. Disk still wins.
    This is the common 'compression noise / small overhead difference' case."""
    rows = [
        row("disk/0001_X.dmx", "disk", "0001", None, 40000),
        row("tele/0001_X.dmx", "telemetry", "0001", None, 50000),
    ]
    assert select_files_for_day(rows) == ["disk/0001_X.dmx"]


def test_tele_wins_when_disk_falls_below_threshold():
    """Disk is 50% of tele — disk materially smaller. Tele wins because the
    recorder-cut-out-mid-minute case means tele actually has more data.
    This is what changed from the original 'absolute disk preference' policy."""
    rows = [
        row("disk/0001_X.dmx", "disk", "0001", None, 25000),
        row("tele/0001_X.dmx", "telemetry", "0001", None, 50000),
    ]
    assert select_files_for_day(rows) == ["tele/0001_X.dmx"]


def test_threshold_zero_means_disk_always_wins():
    """Operator escape hatch: passing disk_size_floor_ratio=0.0 restores the
    old 'absolute disk preference' policy (useful when telemetry is known to
    be unreliable or padded with retransmits)."""
    rows = [
        row("disk/0001_X.dmx", "disk", "0001", None, 5000),
        row("tele/0001_X.dmx", "telemetry", "0001", None, 50000),
    ]
    assert select_files_for_day(rows, disk_size_floor_ratio=0.0) == ["disk/0001_X.dmx"]


def test_threshold_one_means_disk_only_if_at_least_equal():
    """Operator escape hatch: disk_size_floor_ratio=1.0 means tele wins
    whenever it's even slightly bigger (strict 'most data wins, disk only tiebreaks ties')."""
    rows = [
        row("disk/0001_X.dmx", "disk", "0001", None, 45000),
        row("tele/0001_X.dmx", "telemetry", "0001", None, 50000),
    ]
    assert select_files_for_day(rows, disk_size_floor_ratio=1.0) == ["tele/0001_X.dmx"]


def test_disk_complete_discards_tele_stub():
    """Gecko realistic case: disk has full 3-ch .ms.zip for the minute, tele
    has a _CHZ stub. The disk file already covers CHZ — drop the stub."""
    rows = [
        row("disk/STBK_0001.ms.zip", "disk", "0001", None, 30000),
        row("tele/STBK_CHZ_0001.mseed.zip", "telemetry", "0001", "Z", 8000),
    ]
    assert select_files_for_day(rows) == ["disk/STBK_0001.ms.zip"]


# ---------- Tier 3: stubs only — keep one per distinct channel ----------

def test_two_tele_stubs_different_channels_keep_both():
    """Telemetry stubs for two different components in the same minute.
    No overlap — must keep both, otherwise we lose a channel."""
    rows = [
        row("tele/STBK_CHZ_0001.zip", "telemetry", "0001", "Z", 9000),
        row("tele/STBK_CHE_0001.zip", "telemetry", "0001", "E", 9000),
    ]
    out = select_files_for_day(rows)
    assert sorted(out) == ["tele/STBK_CHE_0001.zip", "tele/STBK_CHZ_0001.zip"]


def test_two_tele_stubs_same_channel_larger_wins():
    """Two telemetry stubs for the same component — duplicate. Pick the
    larger (likely the complete one; smaller is a truncated retry)."""
    rows = [
        row("tele/A_CHZ_0001.zip", "telemetry", "0001", "Z", 5000),
        row("tele/B_CHZ_0001.zip", "telemetry", "0001", "Z", 9000),
    ]
    assert select_files_for_day(rows) == ["tele/B_CHZ_0001.zip"]


def test_disk_stub_beats_tele_stub_same_channel():
    """Per-channel data exists on both disk and tele for the same component.
    Disk wins even if smaller (Minimus-style: per-channel files, disk preferred)."""
    rows = [
        row("disk/DDBE_CHZ_0001.zip", "disk", "0001", "Z", 6000),
        row("tele/DDBE_CHZ_0001.zip", "telemetry", "0001", "Z", 9000),
    ]
    assert select_files_for_day(rows) == ["disk/DDBE_CHZ_0001.zip"]


# ---------- Multi-slot integration ----------

def test_multiple_slots_handled_independently():
    """Different policy outcomes at different minute slots in the same day."""
    rows = [
        # Slot 0000: only disk
        row("disk/0000_OUTU.dmx", "disk", "0000"),
        # Slot 0001: disk + tele, both complete -> disk wins
        row("disk/0001_OUTU.dmx", "disk", "0001", None, 50000),
        row("tele/0001_OUTU.dmx", "telemetry", "0001", None, 49000),
        # Slot 0002: only tele, two-channel stubs -> keep both
        row("tele/0002_CHZ.zip", "telemetry", "0002", "Z", 9000),
        row("tele/0002_CHE.zip", "telemetry", "0002", "E", 9000),
    ]
    out = select_files_for_day(rows)
    assert out == [
        "disk/0000_OUTU.dmx",
        "disk/0001_OUTU.dmx",
        "tele/0002_CHE.zip",  # sorted by channel_suffix in tier 3
        "tele/0002_CHZ.zip",
    ]


def test_minimus_realistic_disk_only_three_channels():
    """DDBE clean day: per-channel disk files, three components.
    All three should pass through (tier 3 per-distinct-channel logic)."""
    rows = [
        row("disk/DDBE_CHZ_0001.zip", "disk", "0001", "Z", 8000),
        row("disk/DDBE_CHN_0001.zip", "disk", "0001", "N", 8000),
        row("disk/DDBE_CHE_0001.zip", "disk", "0001", "E", 8000),
    ]
    out = select_files_for_day(rows)
    assert sorted(out) == sorted([
        "disk/DDBE_CHZ_0001.zip",
        "disk/DDBE_CHN_0001.zip",
        "disk/DDBE_CHE_0001.zip",
    ])


# ---------- Edge cases ----------

def test_empty_input():
    """No files for the day (e.g. flagged-skipped). Empty list out."""
    assert select_files_for_day([]) == []


def test_deterministic_order_across_runs():
    """Same input must produce the same output order (no set-iteration leak)."""
    rows = [
        row("a/0002_X.dmx", "disk", "0002"),
        row("a/0001_X.dmx", "disk", "0001"),
        row("a/0003_X.dmx", "disk", "0003"),
    ]
    out1 = select_files_for_day(rows)
    out2 = select_files_for_day(rows)
    assert out1 == out2
    assert out1 == ["a/0001_X.dmx", "a/0002_X.dmx", "a/0003_X.dmx"]


# ---------- Test runner ----------

def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    fails = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            fails.append((name, str(e) or "assertion failed"))
            print(f"  FAIL  {name}: {e or 'assertion failed'}")
        except Exception as e:
            fails.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests)-len(fails)}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

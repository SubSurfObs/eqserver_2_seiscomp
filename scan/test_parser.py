#!/usr/bin/env python3
"""Regression tests for scan/level1.py:parse_filename.

Run: python3 scan/test_parser.py

Each test asserts a specific (filename, dir_station) -> expected fields.
Adding a new case is the fastest way to lock in a filename pattern we've
encountered, so that classifier evolution doesn't quietly break parsing.
"""
from __future__ import annotations
import sys
import unittest
from scan.level1 import parse_filename


def _check(name, dir_sta, **expected):
    got = parse_filename(name, dir_sta)
    mismatches = {k: (got.get(k), v) for k, v in expected.items() if got.get(k) != v}
    return got, mismatches


class TestEchoProDisk(unittest.TestCase):
    """EchoPro disk: DATE_HHMM_SS_STA.dmx[.gz], underscores, SS present."""

    def test_basic_dmx(self):
        got, miss = _check("2001-10-25_0504_08_OUTU.dmx", "OUTU",
                           recorder_type="echopro", source_type="disk", role="waveform",
                           file_year=2001, file_month=10, file_day=25,
                           hhmm="0504", ss="08", filename_station="OUTU",
                           station_mismatch=0, exclude_reason=None)
        self.assertFalse(miss, f"mismatches: {miss}\nfull: {got}")

    def test_gz(self):
        got, miss = _check("2023-11-24_2057_02_ABM5Y.dmx.gz", "ABM5Y",
                           recorder_type="echopro", source_type="disk",
                           hhmm="2057", ss="02", filename_station="ABM5Y",
                           exclude_reason=None)
        self.assertFalse(miss, f"mismatches: {miss}")


class TestEchoProTelemetry(unittest.TestCase):
    """EchoPro telemetry: DATE HHMM SS STA.dmx[.gz], spaces, SS present."""

    def test_basic(self):
        got, miss = _check("2020-01-01 0004 08 CLIF.dmx", "CLIF",
                           recorder_type="echopro", source_type="telemetry",
                           filename_station="CLIF", exclude_reason=None)
        self.assertFalse(miss, f"mismatches: {miss}")

    def test_wrno_flag(self):
        got, miss = _check("1989-08-17 2359 39 ABM5Y.wrno.dmx.gz", "ABM5Y",
                           recorder_type="echopro", source_type="telemetry",
                           file_year=1989, hhmm="2359", ss="39",
                           filename_station="ABM5Y", flags="wrno")
        self.assertFalse(miss, f"mismatches: {miss}")


class TestTriggeredFiles(unittest.TestCase):
    """*.trig.dmx and *.N.trig.dmx must be flagged triggered, station correct."""

    def test_plain_trig(self):
        got, miss = _check("2020-01-01 0004 08 CLIF.trig.dmx", "CLIF",
                           filename_station="CLIF", flags="trig",
                           station_mismatch=0, exclude_reason="triggered")
        self.assertFalse(miss, f"mismatches: {miss}")

    def test_event_indexed_trig(self):
        # Regression: ".1" tail must be stripped so filename_station == 'CLIF'
        for n in range(1, 6):
            with self.subTest(event=n):
                got, miss = _check(f"2020-01-01 0004 08 CLIF.{n}.trig.dmx", "CLIF",
                                   filename_station="CLIF", flags="trig",
                                   station_mismatch=0, exclude_reason="triggered")
                self.assertFalse(miss, f"event={n} mismatches: {miss}")


class TestGecko(unittest.TestCase):
    """Gecko: compact-date disk + dashed-date telemetry. No SS in disk filenames."""

    def test_disk_compact_date(self):
        got, miss = _check("20231029_0001_ABM1Y.ms.zip", "ABM1Y",
                           recorder_type="gecko", source_type="disk", role="waveform",
                           file_year=2023, file_month=10, file_day=29,
                           hhmm="0001", ss=None, filename_station="ABM1Y",
                           exclude_reason=None)
        self.assertFalse(miss, f"mismatches: {miss}")

    def test_tele_dashed_date(self):
        got, miss = _check("2023-10-29 0001 00 ABM1Y.ms.zip", "ABM1Y",
                           recorder_type="gecko", source_type="telemetry",
                           hhmm="0001", ss="00", filename_station="ABM1Y",
                           exclude_reason=None)
        self.assertFalse(miss, f"mismatches: {miss}")


class TestSsMetadata(unittest.TestCase):
    """.ss is metadata-INCLUDE, not a waveform discard."""

    def test_ss_role_metadata(self):
        got, miss = _check("2023-11-05 0000 00 ABM1Y.ss", "ABM1Y",
                           recorder_type="gecko", role="metadata",
                           filename_station="ABM1Y", exclude_reason=None)
        self.assertFalse(miss, f"mismatches: {miss}")
        self.assertNotEqual(got["role"], "waveform")


class TestSingleChannelMseed(unittest.TestCase):
    """*_DHZ.mseed.zip etc. excluded as single_channel for the EchoPro/Gecko bucket.
    NOTE: Guralp/Minimus stations need a recorder-aware OVERRIDE — not handled at
    the parser level. The Phase 2b classifier is the right place for that."""

    def test_dhz_single_channel(self):
        got, miss = _check("2001-10-25 0531 08 OUTU_DHZ.mseed.zip", "OUTU",
                           recorder_type="mseed", source_type="telemetry",
                           filename_station="OUTU", channel_suffix="DHZ",
                           exclude_reason="single_channel")
        self.assertFalse(miss, f"mismatches: {miss}")


class TestEdgeCases(unittest.TestCase):

    def test_unknown_extension(self):
        got, miss = _check("garbage_file.txt", "OUTU",
                           recorder_type="unknown", role="unknown",
                           exclude_reason="unknown_extension")
        self.assertFalse(miss, f"mismatches: {miss}")

    def test_wrong_station_in_filename(self):
        # filename says OUTU, dir says ABM5Y -> station_mismatch should fire
        got, miss = _check("2020-01-01_0000_00_OUTU.dmx", "ABM5Y",
                           filename_station="OUTU", station_mismatch=1,
                           exclude_reason="wrong_station")
        self.assertFalse(miss, f"mismatches: {miss}")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)

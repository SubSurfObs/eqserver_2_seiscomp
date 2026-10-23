#!/usr/bin/env python3
"""Stdlib-only tests for the independent PC-SUDS witness."""

import gzip
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from suds_witness import WitnessError, inspect_file  # noqa: E402


def _ident(short_component: str, long_component: str) -> bytes:
    short = (b"VW  " + b"TEST " + short_component.encode() + struct.pack("<h", 1))
    guts = bytes(64)
    long = (b"VW".ljust(8) + b"TEST".ljust(16)
            + long_component.encode().ljust(8))
    return short + guts + long


def _block(kind: int, body: bytes, data: bytes = b"") -> bytes:
    return struct.pack("<ccHII", b"S", b"6", kind, len(body), len(data)) + body + data


def _descrip(short_component: str, n: int, rate: float, dtype=b"i") -> bytes:
    body = bytearray(96)
    body[:12] = (b"VW  " + b"TEST " + short_component.encode() + struct.pack("<h", 1))
    struct.pack_into("<d", body, 12, 1000.25)
    body[22:23] = dtype
    struct.pack_into("<i", body, 28, n)
    struct.pack_into("<f", body, 32, rate)
    return bytes(body)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        raw = (_block(5, _ident("v", "c03"))
               + _block(7, _descrip("v", 4, 100.0), struct.pack("<hhhh", 1, 2, 3, 4)))
        plain = td / "x.dmx"
        plain.write_bytes(raw)
        zipped = td / "x.dmx.gz"
        with gzip.open(zipped, "wb") as fh:
            fh.write(raw)

        for p in (plain, zipped):
            got = inspect_file(p)
            assert got["waveform_traces"] == 1
            assert got["channels"]["c03"]["declared_samples"] == 4
            assert got["channels"]["c03"]["payload_samples"] == 4
            assert got["traces"][0]["short_channel"] == "v"
            assert got["traces"][0]["end_epoch_exclusive"] == 1000.29

        bad = td / "bad.dmx"
        bad.write_bytes(raw[:-1])
        try:
            inspect_file(bad)
            raise AssertionError("truncated payload was accepted")
        except WitnessError as exc:
            assert "short waveform payload" in str(exc)

    print("all independent SUDS witness tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

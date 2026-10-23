#!/usr/bin/env python3
"""Independent, stdlib-only PC-SUDS waveform witness.

This deliberately imports none of sudspy, qa_verify, or suds_convert.  It
walks the documented on-disc structures and reports raw channel labels,
declared sample counts, payload-derived sample counts, rates and times.  Its
purpose is an independent witness for converter/auditor common-mode failures,
not a replacement waveform decoder.

Format source: PC-SUDS manual appendices A/B (SUDS_STRUCTTAG,
SUDS_STATIONCOMP, SUDS_DESCRIPTRACE).  Intel/x86 files use little endian and
tag bytes ``S6``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path


TAG = struct.Struct("<ccHII")
STATIONCOMP = 5
DESCRIPTRACE = 7
MAX_FIELD_BYTES = 1 << 30

# Widths documented by PC-SUDS.  Complex/vector/tensor encodings are not
# assigned a width here: the witness reports them as unsupported rather than
# guessing a payload sample count.
BYTES_PER_SAMPLE = {
    "s": 2, "q": 2, "u": 2, "i": 2, "r": 2,
    "2": 4, "l": 4, "f": 4, "d": 8,
}


class WitnessError(RuntimeError):
    pass


def _text(raw: bytes) -> str:
    return raw.decode("ascii", "replace").strip("\x00 ")


def _short_ident(raw: bytes) -> dict:
    if len(raw) < 12:
        raise WitnessError(f"station identifier is {len(raw)} bytes, need 12")
    return {
        "network": _text(raw[0:4]),
        "station": _text(raw[4:9]),
        "component": _text(raw[9:10]),
        "instrument_type": struct.unpack_from("<h", raw, 10)[0],
    }


def _long_ident(raw: bytes) -> dict | None:
    # The files in scope append LONGIDENT (8+16+8) to the documented base
    # structures.  Use it only when the complete 32-byte extension exists.
    if len(raw) < 32:
        return None
    tail = raw[-32:]
    return {
        "network": _text(tail[0:8]),
        "station": _text(tail[8:24]),
        "component": _text(tail[24:32]),
    }


def _read_exact(fh, count: int, what: str, offset: int) -> bytes:
    data = fh.read(count)
    if len(data) != count:
        raise WitnessError(
            f"short {what} at offset {offset}: {len(data)} of {count} bytes")
    return data


def _discard_exact(fh, count: int, what: str, offset: int) -> None:
    left = count
    while left:
        chunk = fh.read(min(left, 1 << 20))
        if not chunk:
            raise WitnessError(
                f"short {what} at offset {offset}: missing {left} of {count} bytes")
        left -= len(chunk)


def _open(path: Path):
    return gzip.open(path, "rb") if path.name.lower().endswith(".gz") else path.open("rb")


def inspect_file(path: str | Path) -> dict:
    """Return raw structural evidence for one PC-SUDS file."""
    path = Path(path)
    traces = []
    types = Counter()
    current_station = None
    offset = 0
    n_blocks = 0
    with _open(path) as fh:
        while True:
            raw_tag = fh.read(TAG.size)
            if not raw_tag:
                break
            if len(raw_tag) != TAG.size:
                raise WitnessError(
                    f"short tag at offset {offset}: {len(raw_tag)} of {TAG.size} bytes")
            sync, machine, struct_type, struct_len, data_len = TAG.unpack(raw_tag)
            if (sync, machine) != (b"S", b"6"):
                raise WitnessError(
                    f"bad sync/machine at offset {offset}: {sync!r}{machine!r}")
            if struct_len > MAX_FIELD_BYTES or data_len > MAX_FIELD_BYTES:
                raise WitnessError(
                    f"implausible field length at offset {offset}: "
                    f"struct={struct_len}, data={data_len}")
            body_offset = offset + TAG.size
            body = _read_exact(fh, struct_len, "structure", body_offset)
            types[str(struct_type)] += 1
            n_blocks += 1

            if struct_type == STATIONCOMP:
                if len(body) < 76:
                    raise WitnessError(
                        f"short STATIONCOMP at offset {offset}: {len(body)} bytes")
                current_station = {
                    "short": _short_ident(body),
                    "long": _long_ident(body) if len(body) >= 108 else None,
                    "offset": offset,
                }
                _discard_exact(fh, data_len, "STATIONCOMP payload", body_offset + struct_len)
            elif struct_type == DESCRIPTRACE:
                if len(body) < 64:
                    raise WitnessError(
                        f"short DESCRIPTRACE at offset {offset}: {len(body)} bytes")
                declared = struct.unpack_from("<i", body, 28)[0]
                rate = float(struct.unpack_from("<f", body, 32)[0])
                begin = float(struct.unpack_from("<d", body, 12)[0])
                dtype = _text(body[22:23])
                bps = BYTES_PER_SAMPLE.get(dtype)
                payload_samples = data_len // bps if bps else None
                remainder = data_len % bps if bps else None
                ident = current_station or {
                    "short": _short_ident(body),
                    "long": _long_ident(body) if len(body) >= 96 else None,
                    "offset": None,
                }
                preferred = ident.get("long") or ident["short"]
                record = {
                    "block_offset": offset,
                    "stationcomp_offset": ident.get("offset"),
                    "raw_network": preferred.get("network", ""),
                    "raw_station": preferred.get("station", ""),
                    "raw_channel": preferred.get("component", ""),
                    "short_channel": ident["short"].get("component", ""),
                    "datatype": dtype,
                    "bytes_per_sample": bps,
                    "declared_samples": declared,
                    "payload_bytes": data_len,
                    "payload_samples": payload_samples,
                    "payload_remainder_bytes": remainder,
                    "sample_count_agrees": (
                        bps is not None and remainder == 0 and declared == payload_samples),
                    "start_epoch": begin,
                    "rate_hz": rate,
                    "end_epoch_exclusive": (
                        begin + declared / rate
                        if declared >= 0 and math.isfinite(rate) and rate > 0 else None),
                }
                traces.append(record)
                _discard_exact(fh, data_len, "waveform payload", body_offset + struct_len)
            else:
                _discard_exact(fh, data_len, "data payload", body_offset + struct_len)

            offset += TAG.size + struct_len + data_len

    by_channel = {}
    for tr in traces:
        ch = tr["raw_channel"] or tr["short_channel"] or "(blank)"
        a = by_channel.setdefault(ch, {
            "traces": 0, "declared_samples": 0, "payload_samples": 0,
            "sample_count_disagreements": 0,
        })
        a["traces"] += 1
        a["declared_samples"] += tr["declared_samples"]
        if tr["payload_samples"] is not None:
            a["payload_samples"] += tr["payload_samples"]
        if not tr["sample_count_agrees"]:
            a["sample_count_disagreements"] += 1

    return {
        "path": str(path),
        "bytes_uncompressed": offset,
        "blocks": n_blocks,
        "structure_types": dict(sorted(types.items(), key=lambda x: int(x[0]))),
        "waveform_traces": len(traces),
        "channels": by_channel,
        "traces": traces,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", help="PC-SUDS .dmx/.sud files (optionally .gz)")
    ap.add_argument("--compact", action="store_true", help="one JSON object per line")
    args = ap.parse_args()
    failed = False
    for name in args.paths:
        try:
            result = inspect_file(name)
        except Exception as exc:
            failed = True
            result = {"path": name, "error": f"{type(exc).__name__}: {exc}"}
        if args.compact:
            print(json.dumps(result, sort_keys=True))
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

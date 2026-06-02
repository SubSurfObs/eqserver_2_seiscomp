#!/usr/bin/env python3
"""check_registry.py — sanity guard for metadata/station_registry.yaml.

Verifies that no top-level station block has duplicate keys. YAML safe_load
silently keeps the LAST value when a key is duplicated within a mapping, so
duplicates corrupt the parse without raising — exactly the bug that hit the
2026-06-01 DU promotions (commit 39b22e9), where new fields were inserted
above the stale skeleton fields and silently overridden.

Run from repo root before committing registry changes:
    python3 metadata/check_registry.py

Exits non-zero with a per-block report if any duplicates are found.
Intended for use as a pre-commit hook or manual sanity check.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

PATH = Path(__file__).parent / "station_registry.yaml"
BLOCK_RE = re.compile(r"^([A-Za-z0-9_]+):\s*$")
KEY_RE = re.compile(r"^  ([a-z_]+):")


def main() -> int:
    lines = PATH.read_text().splitlines()

    blocks: list[tuple[str, int, int]] = []
    cur_name: str | None = None
    cur_start: int | None = None
    for i, ln in enumerate(lines):
        m = BLOCK_RE.match(ln)
        if m:
            if cur_name is not None:
                blocks.append((cur_name, cur_start, i))
            cur_name = m.group(1)
            cur_start = i
    if cur_name is not None:
        blocks.append((cur_name, cur_start, len(lines)))

    broken: list[tuple[str, dict[str, int]]] = []
    for name, s, e in blocks:
        seen: dict[str, int] = {}
        for i in range(s + 1, e):
            m = KEY_RE.match(lines[i])
            if m:
                k = m.group(1)
                seen[k] = seen.get(k, 0) + 1
        dups = {k: v for k, v in seen.items() if v > 1}
        if dups:
            broken.append((name, dups))

    if not broken:
        print(f"OK: {len(blocks)} station blocks scanned, no duplicate keys.")
        return 0

    print(f"FAIL: {len(broken)} station block(s) have duplicate keys:", file=sys.stderr)
    for name, dups in broken:
        print(f"  {name}: {dups}", file=sys.stderr)
    print(
        "\nDuplicate keys are silently overridden by YAML safe_load (the later "
        "value wins). Fix: keep the intended value, delete the others.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

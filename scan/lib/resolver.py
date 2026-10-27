#!/usr/bin/env python3
"""resolver — explicit-precedence overlay of selected waveform segments.

Implements the Codex design recommendation (docs/
codex_f5_f6_design_recommendation.md, adopted 2026-10-27): the selection's
priorities — not Python list order, format dispatch order, write order or
an overlap-duration threshold — decide which samples survive.

Input: per (target SEED id, rate), a list of SEGMENTS — already read,
relabeled and drift-split by the format engines, so each segment's
stated start anchors a sample grid the as-timestamped ruling protects:

    {"t0": float epoch seconds, "rate": float, "data": sequence,
     "priority": int (0 = preferred), "path": str}

Overlay rule (order-free):
  1. Segments resolve in ascending priority; ties process in
     deterministic (t0, path) order.
  2. A segment occupies the half-open time span [t0, t0 + n/rate).
  3. A lower-priority segment contributes ONLY samples whose stated
     times fall outside every occupied span; the retained pieces keep
     their own stated grid (a new output segment anchors at the stated
     time of its first retained sample — never shifted onto another
     grid).
  4. Nothing is averaged, trimmed by duration heuristics, or decided by
     which copy was read or written first.

The result is a list of output segments (t0, data) whose spans never
overlap. Pure Python + optional numpy data; no obspy dependency, so the
logic is unit-testable anywhere.
"""

EPS = 1e-9


def _subtract_spans(lo, hi, occupied):
    """Sub-intervals of [lo, hi) not covered by `occupied` (sorted,
    non-overlapping [a, b) pairs)."""
    out = []
    cur = lo
    for a, b in occupied:
        if b <= cur + EPS:
            continue
        if a >= hi - EPS:
            break
        if a > cur + EPS:
            out.append((cur, min(a, hi)))
        cur = max(cur, b)
        if cur >= hi - EPS:
            break
    if cur < hi - EPS:
        out.append((cur, hi))
    return out


def _insert_span(occupied, lo, hi):
    """Insert [lo, hi) into the sorted non-overlapping span list."""
    out = []
    placed = False
    for a, b in occupied:
        if b < lo - EPS:
            out.append((a, b))
        elif hi < a - EPS:
            if not placed:
                out.append((lo, hi))
                placed = True
            out.append((a, b))
        else:
            lo, hi = min(lo, a), max(hi, b)
    if not placed:
        out.append((lo, hi))
    out.sort()
    return out


def resolve_segments(segments):
    """Overlay `segments` by priority. Returns output segments as dicts
    {"t0", "rate", "data", "path"} with non-overlapping spans, sorted by
    time. Segments must share one sample rate (the caller resolves each
    (SEED id, rate) stream separately — different rates are different
    output streams, not copies)."""
    if not segments:
        return []
    rates = {round(float(s["rate"]), 6) for s in segments}
    if len(rates) != 1:
        raise ValueError(f"resolve_segments: mixed rates {sorted(rates)}")
    rate = float(segments[0]["rate"])
    dt = 1.0 / rate
    # Identical path authorized twice (accidental routing duplication):
    # collapse by identity before any timestamp inspection (design rule).
    seen, ordered = set(), []
    for s in sorted(segments,
                    key=lambda s: (int(s.get("priority", 0)),
                                   float(s["t0"]), str(s.get("path", "")))):
        key = (s.get("path"), round(float(s["t0"]) * rate), len(s["data"]))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(s)

    occupied = []  # sorted non-overlapping [a, b) spans
    out = []
    for s in ordered:
        t0 = float(s["t0"])
        data = s["data"]
        n = len(data)
        if n == 0:
            continue
        for lo, hi in _subtract_spans(t0, t0 + n * dt, occupied):
            # First retained sample at or after lo, on THIS segment's grid.
            i0 = max(0, int(-(-((lo - t0) * rate - EPS) // 1)))  # ceil
            i1 = min(n, int(((hi - t0) * rate) + EPS))
            if i1 <= i0:
                continue
            piece_t0 = t0 + i0 * dt
            out.append({"t0": piece_t0, "rate": rate,
                        "data": data[i0:i1], "path": s.get("path")})
            occupied = _insert_span(occupied, piece_t0,
                                    piece_t0 + (i1 - i0) * dt)
    out.sort(key=lambda s: s["t0"])
    return out

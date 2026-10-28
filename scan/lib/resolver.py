#!/usr/bin/env python3
"""resolver — explicit-precedence overlay of selected waveform segments.

v2 (2026-10-29, after Codex review `2026-10-28_resolver_topology_gate.md`
F2): ALL time arithmetic is EXACT INTEGER NANOSECONDS. The previous
version subtracted Unix-epoch floats and truncated to sample indices; at
2012–2026 timestamps the float error exceeded its epsilon and boundary
samples of winning segments were silently dropped (Codex measured
hundreds of cases across archive rates; the proving days' "0.5 s
missing" was this defect).

Input: per (target SEED id, rate), a list of SEGMENTS — already read,
relabeled and drift-split per file by the format adapters, so each
segment's stated start anchors a sample grid the as-timestamped ruling
protects:

    {"t0_ns": int (UTC nanoseconds), "rate": float, "data": sequence,
     "priority": int (0 = preferred), "path": str}

(The compatibility key "t0" in float seconds is accepted and converted
once, at the boundary, with round() — never used in arithmetic.)

Overlay rule (order-free):
  1. Segments resolve in ascending priority; ties process in
     deterministic (t0_ns, path) order.
  2. A segment occupies the half-open span [t0_ns, t0_ns + n*dt_ns).
  3. A lower-priority segment contributes ONLY samples whose stated
     times fall outside every occupied span; retained pieces keep their
     own stated grid (a new output segment anchors at the stated time of
     its first retained sample — never shifted onto another grid).
  4. Nothing is averaged, trimmed by duration heuristics, or decided by
     read/write order.

Every archive sample rate divides 1e9 evenly (40, 50, 100, 200, 250,
400, 500, 800, 1000 sps); a rate that does not is refused rather than
approximated.
"""

NS = 1_000_000_000


def _dt_ns(rate):
    """Exact sample period in integer nanoseconds, or refuse."""
    r = float(rate)
    if r <= 0:
        raise ValueError(f"non-positive rate {rate!r}")
    dt, rem = divmod(NS, round(r))
    if rem != 0 or abs(r - round(r)) > 1e-9:
        raise ValueError(
            f"rate {rate!r} has no exact integer-ns period; extend the "
            f"resolver with rational arithmetic before converting it")
    return int(dt)


def _seg_t0_ns(s):
    if "t0_ns" in s:
        return int(s["t0_ns"])
    return round(float(s["t0"]) * NS)


def _subtract_spans_ns(lo, hi, occupied):
    """Sub-intervals of [lo, hi) not covered by `occupied` (sorted,
    non-overlapping integer [a, b) pairs). Pure integer arithmetic."""
    out = []
    cur = lo
    for a, b in occupied:
        if b <= cur:
            continue
        if a >= hi:
            break
        if a > cur:
            out.append((cur, min(a, hi)))
        cur = max(cur, b)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return out


def _insert_span_ns(occupied, lo, hi):
    """Insert [lo, hi) into the sorted non-overlapping integer span list."""
    out = []
    placed = False
    for a, b in occupied:
        if b < lo:
            out.append((a, b))
        elif hi < a:
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
    {"t0_ns", "rate", "data", "path"} with non-overlapping spans, sorted
    by time. Segments must share one sample rate (the caller resolves
    each (SEED id, rate) stream separately — different rates are
    different output streams, not copies of one another)."""
    if not segments:
        return []
    rates = {round(float(s["rate"]), 6) for s in segments}
    if len(rates) != 1:
        raise ValueError(f"resolve_segments: mixed rates {sorted(rates)}")
    rate = float(segments[0]["rate"])
    dt = _dt_ns(rate)

    # Identical path authorized twice (accidental routing duplication):
    # collapse by identity before any timestamp inspection (design rule).
    seen, ordered = set(), []
    for s in sorted(segments,
                    key=lambda s: (int(s.get("priority", 0)),
                                   _seg_t0_ns(s), str(s.get("path", "")))):
        t0 = _seg_t0_ns(s)
        key = (s.get("path"), t0, len(s["data"]))
        if key in seen:
            continue
        seen.add(key)
        ordered.append((t0, s))

    occupied = []  # sorted non-overlapping [a, b) integer-ns spans
    out = []
    for t0, s in ordered:
        data = s["data"]
        n = len(data)
        if n == 0:
            continue
        for lo, hi in _subtract_spans_ns(t0, t0 + n * dt, occupied):
            # First retained sample at or after lo, on THIS segment's
            # grid: exact integer ceiling division, no floats.
            i0 = max(0, -((t0 - lo) // dt))
            # Last retained sample strictly before hi.
            i1 = min(n, -((t0 - hi) // dt))
            if i1 <= i0:
                continue
            piece_t0 = t0 + i0 * dt
            out.append({"t0_ns": piece_t0, "rate": rate,
                        "data": data[i0:i1], "path": s.get("path")})
            occupied = _insert_span_ns(occupied, piece_t0,
                                       piece_t0 + (i1 - i0) * dt)
    out.sort(key=lambda s: s["t0_ns"])
    return out

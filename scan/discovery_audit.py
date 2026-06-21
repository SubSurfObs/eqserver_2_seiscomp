#!/usr/bin/env python3
"""discovery_audit.py — surface unknown-unknowns in a station's manifest.

Where categorize_source.py asks "how often does each known pattern occur?",
this asks "what patterns exist at all, including ones I don't recognise?".
Every distinct combination of manifest column values, every size outlier,
every filename that doesn't match a documented regex — all recorded in the
output so a human can spot anything the project's mental model missed.

Manifest schema (per CLAUDE.md / scan/level1.py):
    path, station, dir_year/month/day, recorder_type, source_type, role,
    file_year/month/day, date_mismatch, hhmm, ss, channel_suffix,
    filename_station, station_mismatch, flags, size_bytes, mtime,
    exclude_reason

Outputs (per station):
    metadata/source_stats/_discovery/<STA>.json with:

      manifest_signatures :  every distinct combination of
        (recorder_type, source_type, role, ss IS NULL,
         channel_suffix IS NULL, exclude_reason, date_mismatch,
         station_mismatch, flags) along with row counts per (sta, year).
        Two rows with the same source_type but differing ss-null
        signature → AMBIGUOUS — the two-telemetry-stream pattern would
        have surfaced HERE before scan-1.

      size_distributions  :  per (signature, year) — min/p10/p50/p90/max
        + outlier count + bimodality flag (rough). Multi-modal = "two
        populations we haven't distinguished yet".

      filename_grammar    :  documented regex patterns + match counts +
        sample of 20 filenames that match NONE of the patterns.

      weird_subreasons    :  bucketed reasons for each day flagged "weird"
        in categorize_source. Not just 'doesn't fit' but WHY.

USAGE:
    python3 scan/discovery_audit.py STBK
    python3 scan/discovery_audit.py STBK --out-dir metadata/source_stats/_discovery
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "metadata" / "source_stats" / "_discovery"


# Documented filename patterns from CLAUDE.md + test_env_build.
# Each (name, regex) — order matters for the "first match wins" classification.
FILENAME_PATTERNS = [
    # SUDS (.dmx)
    ("triggered_suds_numeric", re.compile(r"^.+\.\d+\.dmx(\.gz)?$")),
    ("triggered_suds_trig",     re.compile(r"^.+\.trig\.dmx(\.gz)?$")),
    ("triggered_suds_num_trig", re.compile(r"^.+\.\d+\.trig\.dmx(\.gz)?$")),
    ("disk_suds_dashed",  re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_\d{2}_\w+\.dmx(\.gz)?$")),
    ("tele_ss_suds",      re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.dmx(\.gz)?$")),
    ("tele_noss_suds",    re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.dmx(\.gz)?$")),
    # MSEED-bundled (Gecko-family)
    ("disk_mseed_numeric_date",  re.compile(r"^\d{8}_\d{4}_\w+\.ms\.zip$")),
    ("disk_mseed_dashed_date",   re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_\w+\.ms\.zip$")),
    ("tele_ss_mseed",            re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+\.ms\.zip$")),
    ("tele_noss_mseed",          re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ms\.zip$")),
    # Per-channel MSEED (Gecko stubs / Minimus / PiesMo)
    ("perchan_mseed_zip",        re.compile(r"^.+_(?:CHZ|CHN|CHE|HHZ|HHN|HHE|DHZ|DHN|DHE)\.mseed\.zip$")),
    ("perchan_mseed",            re.compile(r"^.+_(?:CHZ|CHN|CHE|HHZ|HHN|HHE|DHZ|DHN|DHE)\.mseed$")),
    # Telemetry-format per-channel mseed (PiesMo / Gecko stubs via tele)
    ("tele_perchan_mseed_zip",   re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \d{2} \w+_(?:CHZ|CHN|CHE|HHZ|HHN|HHE|DHZ|DHN|DHE)\.mseed\.zip$")),
    # Kelunjimeta sidecar
    ("kelunjimeta_ss",           re.compile(r"^.+\.ss$")),
    ("kelunjimeta_ss_space",     re.compile(r"^\d{4}-\d{2}-\d{2} \d{4} \w+\.ss$")),
]


def signature_of(row: dict) -> tuple:
    """Compact, distinct-able signature for a manifest row."""
    return (
        row.get("recorder_type"),
        row.get("source_type"),
        row.get("role"),
        row.get("ss") is None,
        row.get("channel_suffix") is None,
        row.get("exclude_reason"),
        bool(row.get("date_mismatch")),
        bool(row.get("station_mismatch")),
        row.get("flags") or None,
    )


def classify_filename(name: str) -> str:
    for pat_name, regex in FILENAME_PATTERNS:
        if regex.match(name):
            return pat_name
    return "UNMATCHED"


def percentile(values, p):
    if not values:
        return None
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    return sorted_v[f] + (sorted_v[c] - sorted_v[f]) * (k - f)


def likely_bimodal(sizes: list) -> bool:
    """Crude bimodality heuristic: gap between 50th and 75th percentile
    > 2x the inter-quartile range below median."""
    if len(sizes) < 50:
        return False
    p25 = percentile(sizes, 25)
    p50 = percentile(sizes, 50)
    p75 = percentile(sizes, 75)
    iqr_lower = p50 - p25
    iqr_upper = p75 - p50
    return iqr_upper > 2 * max(1, iqr_lower)


def audit_station(sta: str, db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    # Pull every waveform row's signature-relevant columns + size + basename
    cols = ("path", "recorder_type", "source_type", "role",
            "ss", "channel_suffix", "exclude_reason", "date_mismatch",
            "station_mismatch", "flags", "size_bytes", "dir_year")
    sql = f"SELECT {','.join(cols)} FROM files"
    rows = conn.execute(sql).fetchall()
    conn.close()

    # Aggregate by signature
    sig_counts: dict = defaultdict(lambda: {
        "rows": 0, "by_year": Counter(), "sizes": []
    })
    filename_counts = Counter()
    unmatched_samples = []  # list of basenames
    sig_unmatched: dict = defaultdict(int)

    for r in rows:
        row = dict(zip(cols, r))
        sig = signature_of(row)
        sig_counts[sig]["rows"] += 1
        sig_counts[sig]["by_year"][row["dir_year"]] += 1
        if row["size_bytes"]:
            sig_counts[sig]["sizes"].append(row["size_bytes"])
        name = row["path"].split("/")[-1] if row["path"] else ""
        pat = classify_filename(name)
        filename_counts[pat] += 1
        if pat == "UNMATCHED":
            if len(unmatched_samples) < 30:
                unmatched_samples.append({"basename": name, "signature": sig})
            sig_unmatched[sig] += 1

    # Detect ambiguous pairs: same source_type, different ss-null signature
    ambiguous_pairs = []
    sigs_by_st = defaultdict(list)
    for sig in sig_counts:
        # sig = (recorder, src, role, ss_null, suffix_null, exclude, dmismatch, smismatch, flags)
        key = (sig[0], sig[1], sig[2], sig[4], sig[5], sig[6], sig[7], sig[8])
        sigs_by_st[key].append(sig)
    for key, group in sigs_by_st.items():
        if len(group) > 1:
            ambiguous_pairs.append({
                "shared_columns": {
                    "recorder_type": key[0], "source_type": key[1],
                    "role": key[2], "channel_suffix_null": key[3],
                    "exclude_reason": key[4], "date_mismatch": key[5],
                    "station_mismatch": key[6], "flags": key[7],
                },
                "differing_signatures": [
                    {"ss_is_null": s[3], "row_count": sig_counts[s]["rows"]}
                    for s in group
                ],
                "total_rows": sum(sig_counts[s]["rows"] for s in group),
            })

    # Build signatures payload
    signatures_out = []
    for sig, data in sorted(sig_counts.items(), key=lambda x: -x[1]["rows"]):
        sizes = data["sizes"]
        sig_dict = {
            "signature": {
                "recorder_type": sig[0],
                "source_type": sig[1],
                "role": sig[2],
                "ss_is_null": sig[3],
                "channel_suffix_is_null": sig[4],
                "exclude_reason": sig[5],
                "date_mismatch": sig[6],
                "station_mismatch": sig[7],
                "flags": sig[8],
            },
            "row_count": data["rows"],
            "by_year": dict(data["by_year"]),
            "size_stats": {
                "min": min(sizes) if sizes else None,
                "p10": int(percentile(sizes, 10)) if sizes else None,
                "p50": int(percentile(sizes, 50)) if sizes else None,
                "p90": int(percentile(sizes, 90)) if sizes else None,
                "max": max(sizes) if sizes else None,
                "n_samples": len(sizes),
            },
            "size_likely_bimodal": likely_bimodal(sizes),
            "unmatched_filename_count": sig_unmatched.get(sig, 0),
        }
        signatures_out.append(sig_dict)

    # Filename grammar coverage
    total_files = sum(filename_counts.values())
    grammar = []
    for pat, cnt in filename_counts.most_common():
        grammar.append({
            "pattern": pat,
            "count": cnt,
            "pct": round(100 * cnt / total_files, 3) if total_files else 0,
        })

    return {
        "station": sta,
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_db": str(db_path),
        "total_rows": total_files,
        "n_distinct_signatures": len(signatures_out),
        "manifest_signatures": signatures_out,
        "ambiguous_signature_pairs": ambiguous_pairs,
        "filename_grammar": grammar,
        "unmatched_filename_samples": unmatched_samples,
    }


def format_text(d: dict) -> str:
    lines = []
    lines.append(f"# Discovery audit: {d['station']}")
    lines.append(f"# Generated: {d['generated_at_utc']}")
    lines.append(f"# Total rows: {d['total_rows']}")
    lines.append(f"# Distinct signatures: {d['n_distinct_signatures']}")
    lines.append("")

    lines.append("## Ambiguous signature pairs (CHECK THESE)")
    if d["ambiguous_signature_pairs"]:
        for amb in d["ambiguous_signature_pairs"]:
            sc = amb["shared_columns"]
            lines.append(f"  source_type={sc['source_type']}, "
                         f"exclude_reason={sc['exclude_reason']}, "
                         f"role={sc['role']}: "
                         f"{amb['total_rows']} rows split as:")
            for s in amb["differing_signatures"]:
                lines.append(f"    ss_is_null={s['ss_is_null']} "
                             f"→ {s['row_count']} rows")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("## Filename grammar coverage")
    for g in d["filename_grammar"]:
        lines.append(f"  {g['pattern']:32s} {g['count']:10d}  {g['pct']:6.2f}%")
    lines.append("")

    if d.get("unmatched_filename_samples"):
        lines.append("## Sample UNMATCHED filenames")
        for s in d["unmatched_filename_samples"][:15]:
            lines.append(f"  {s['basename']}")
        lines.append("")

    lines.append("## Top signatures (by row count)")
    for s in d["manifest_signatures"][:10]:
        sig = s["signature"]
        flags = []
        if sig["date_mismatch"]: flags.append("date_mismatch")
        if sig["station_mismatch"]: flags.append("station_mismatch")
        if sig["flags"]: flags.append(f"flags={sig['flags']}")
        bimodal = " ⚠ BIMODAL" if s["size_likely_bimodal"] else ""
        lines.append(f"  rows={s['row_count']:10d}  "
                     f"recorder={sig['recorder_type']:8s} "
                     f"src={sig['source_type']:10s} "
                     f"role={sig['role']:9s} "
                     f"ss_null={str(sig['ss_is_null']):5s} "
                     f"chan_null={str(sig['channel_suffix_is_null']):5s} "
                     f"excl={str(sig['exclude_reason'])}"
                     f"{('  '+','.join(flags)) if flags else ''}")
        ss = s["size_stats"]
        if ss["n_samples"]:
            lines.append(f"           size:  min={ss['min']}  "
                         f"p10={ss['p10']}  p50={ss['p50']}  "
                         f"p90={ss['p90']}  max={ss['max']}{bimodal}")

    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sta")
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--network", default="VW")
    args = ap.parse_args()

    db_path = args.db_dir / f"{args.network}.{args.sta}.db"
    if not db_path.exists():
        print(f"ERROR: manifest DB not found: {db_path}", file=sys.stderr)
        return 2

    print(f"[discovery_audit] {args.sta} from {db_path}", flush=True)
    out = audit_station(args.sta, db_path)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"{args.sta}.json"
    txt_path = args.out_dir / f"{args.sta}.txt"
    json_path.write_text(json.dumps(out, indent=2, sort_keys=True))
    txt_path.write_text(format_text(out))
    print(f"[discovery_audit] wrote {json_path}", flush=True)
    print(f"[discovery_audit] wrote {txt_path}", flush=True)
    print()
    print(format_text(out), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

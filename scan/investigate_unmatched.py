#!/usr/bin/env python3
"""investigate_unmatched.py — characterise the 4M filenames that didn't
match any documented grammar.

Step 1: Pool the per-station unmatched samples from
        metadata/source_stats/_discovery/<STA>.json (30 each, 41 stations).
        Tokenise into shape templates. Network summary of shape→count.

Step 2: For each station with >1000 unmatched in the manifest, query the
        station DB directly for ALL unmatched basenames. Aggregate by
        shape template + by directory-prefix (year/month). Surface
        location concentrations.

Output:
  metadata/source_stats/_unmatched/_network_summary.json
  metadata/source_stats/_unmatched/<STA>.json    (only for top offenders)
  metadata/source_stats/_unmatched/_network_summary.txt
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISC_DIR = REPO / "metadata" / "source_stats" / "_discovery"
OUT_DIR = REPO / "metadata" / "source_stats" / "_unmatched"
DEFAULT_DB_DIR = Path("/home/unimelb.edu.au/dsand/station_dbs")

# Re-use discovery's grammar list — import via sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from discovery_audit import FILENAME_PATTERNS, classify_filename  # noqa: E402

# Threshold: stations with more than this many unmatched get a manifest-level
# deep dive. Below the threshold, we trust the 30-sample pool.
DEEP_DIVE_THRESHOLD = 1000


# --------------------------------------------------------------------------
# Tokenisation: collapse digit/hex/alpha runs into a "shape template"
# --------------------------------------------------------------------------

def shape(basename: str) -> str:
    """Convert a basename into a coarse shape template:
       - digit runs → D{n}
       - lowercase hex runs (no digits) → h{n}   (these tend to be FAT hash)
       - upper hex runs (the 8.3 truncation case '~E1F2') → H{n}
       - other lowercase letter runs → l{n}
       - other upper letter runs → A{n}
       - literal separators (- _ . ~ space) preserved verbatim
    """
    out = []
    i = 0
    while i < len(basename):
        c = basename[i]
        if c.isdigit():
            j = i
            while j < len(basename) and basename[j].isdigit():
                j += 1
            out.append(f"D{j-i}")
            i = j
        elif c.isupper():
            j = i
            while j < len(basename) and basename[j].isupper():
                j += 1
            run = basename[i:j]
            # Hex-like (only A-F)? mark separately.
            if all(ch in "ABCDEF" for ch in run):
                out.append(f"H{j-i}")
            else:
                out.append(f"A{j-i}")
            i = j
        elif c.islower():
            j = i
            while j < len(basename) and basename[j].islower():
                j += 1
            run = basename[i:j]
            if all(ch in "abcdef" for ch in run):
                out.append(f"h{j-i}")
            else:
                out.append(f"l{j-i}")
            i = j
        else:
            # Literal separator/punctuation — keep verbatim
            out.append(c)
            i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Step 1: pool the 30-sample lists
# --------------------------------------------------------------------------

def pool_samples() -> dict:
    """Read every _discovery/<STA>.json, collect unmatched_filename_samples
    + the unmatched count from filename_grammar."""
    samples_per_station: dict = {}
    counts_per_station: dict = {}
    for f in sorted(DISC_DIR.glob("*.json")):
        d = json.loads(f.read_text())
        sta = d["station"]
        samples_per_station[sta] = [
            s["basename"] for s in d.get("unmatched_filename_samples", [])
            if s.get("basename")
        ]
        # Lookup unmatched count in grammar
        cnt = 0
        for g in d.get("filename_grammar", []):
            if g["pattern"] == "UNMATCHED":
                cnt = g["count"]
                break
        counts_per_station[sta] = cnt
    return {"samples": samples_per_station, "counts": counts_per_station}


# --------------------------------------------------------------------------
# Step 2: deep dive on top offenders — query manifest directly
# --------------------------------------------------------------------------

def deep_dive(sta: str, db_path: Path) -> dict:
    """For one station, walk the manifest and find every basename that
    matches none of the documented FILENAME_PATTERNS. Tokenise + group
    by shape and by directory prefix."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT path FROM files").fetchall()
    conn.close()

    shape_counts: Counter = Counter()
    dir_counts: Counter = defaultdict(int)
    shape_samples: dict = defaultdict(list)
    total_unmatched = 0
    for (path,) in rows:
        name = path.split("/")[-1] if path else ""
        if classify_filename(name) != "UNMATCHED":
            continue
        total_unmatched += 1
        sh = shape(name)
        shape_counts[sh] += 1
        # Keep first 3 samples per shape for the report
        if len(shape_samples[sh]) < 3:
            shape_samples[sh].append(name)
        # Directory: last 4 components (year/month/day/basename → keep year/month)
        parts = path.split("/") if path else []
        if len(parts) >= 4:
            dir_key = "/".join(parts[-4:-1])  # year/month/day
            # Coarsen further to just year/month for the count
            year_month = "/".join(parts[-4:-2])
            dir_counts[year_month] += 1

    return {
        "station": sta,
        "total_unmatched": total_unmatched,
        "top_shapes": [
            {"shape": sh, "count": cnt, "samples": shape_samples[sh]}
            for sh, cnt in shape_counts.most_common(15)
        ],
        "n_distinct_shapes": len(shape_counts),
        "top_directories": [
            {"year_month": ym, "count": cnt}
            for ym, cnt in sorted(dir_counts.items(), key=lambda x: -x[1])[:15]
        ],
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--network", default="VW")
    ap.add_argument("--no-deep-dive", action="store_true",
                    help="step 1 only; skip the per-station manifest scan")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print("[step 1] pooling unmatched samples from _discovery outputs", flush=True)
    pooled = pool_samples()

    # Network-wide shape histogram from pooled samples
    pooled_shapes: Counter = Counter()
    pooled_shape_samples: dict = defaultdict(list)
    for sta, samples in pooled["samples"].items():
        for s in samples:
            sh = shape(s)
            pooled_shapes[sh] += 1
            if len(pooled_shape_samples[sh]) < 5:
                pooled_shape_samples[sh].append((sta, s))

    network_summary = {
        "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_stations": len(pooled["counts"]),
        "total_unmatched_in_manifest": sum(pooled["counts"].values()),
        "stations_by_unmatched_count": sorted(
            pooled["counts"].items(), key=lambda x: -x[1]),
        "pooled_sample_shape_counts": [
            {"shape": sh, "n_samples": cnt,
             "examples": pooled_shape_samples[sh][:3]}
            for sh, cnt in pooled_shapes.most_common(25)
        ],
    }

    # Step 2: per-station deep dive for top offenders
    top_offenders = [
        sta for sta, n in pooled["counts"].items()
        if n >= DEEP_DIVE_THRESHOLD
    ]
    print(f"[step 2] deep dive on {len(top_offenders)} stations "
          f"(>={DEEP_DIVE_THRESHOLD} unmatched)", flush=True)
    deep_results = {}
    for sta in top_offenders:
        db_path = args.db_dir / f"{args.network}.{sta}.db"
        if not db_path.exists():
            continue
        print(f"  scanning {sta}...", flush=True)
        r = deep_dive(sta, db_path)
        deep_results[sta] = r
        # Write per-station detail
        (args.out_dir / f"{sta}.json").write_text(
            json.dumps(r, indent=2, sort_keys=True))

    network_summary["deep_dive_stations"] = list(deep_results.keys())

    # Network-wide shape histogram from deep-dive results
    deep_shapes: Counter = Counter()
    for sta, r in deep_results.items():
        for s in r["top_shapes"]:
            deep_shapes[s["shape"]] += s["count"]
    network_summary["deep_dive_shape_counts"] = [
        {"shape": sh, "count": cnt}
        for sh, cnt in deep_shapes.most_common(25)
    ]

    summary_path = args.out_dir / "_network_summary.json"
    summary_path.write_text(json.dumps(network_summary, indent=2, sort_keys=True))
    print(f"[wrote] {summary_path}", flush=True)

    # Human-readable summary
    txt_lines = []
    txt_lines.append("# Unmatched-filename investigation network summary")
    txt_lines.append(f"# Generated: {network_summary['generated_at_utc']}")
    txt_lines.append(f"# Stations: {network_summary['n_stations']}")
    txt_lines.append(f"# Total unmatched basenames in manifests: "
                     f"{network_summary['total_unmatched_in_manifest']:,}")
    txt_lines.append("")
    txt_lines.append("## Stations with the most unmatched (top 15)")
    for sta, n in network_summary["stations_by_unmatched_count"][:15]:
        if n > 0:
            txt_lines.append(f"  {sta:8s}  {n:>10,}")
    txt_lines.append("")
    txt_lines.append("## Pooled-sample shapes (from 30-sample-per-station discovery)")
    for s in network_summary["pooled_sample_shape_counts"][:15]:
        examples = ", ".join(f"{sta}:{name}" for sta, name in s["examples"])
        txt_lines.append(f"  shape={s['shape']:30s}  n_samples={s['n_samples']:4d}  e.g. {examples}")
    txt_lines.append("")
    txt_lines.append("## Deep-dive shapes (from full manifest scan of top offenders)")
    for s in network_summary["deep_dive_shape_counts"][:20]:
        txt_lines.append(f"  shape={s['shape']:30s}  count={s['count']:>10,}")
    summary_txt_path = args.out_dir / "_network_summary.txt"
    summary_txt_path.write_text("\n".join(txt_lines) + "\n")
    print(f"[wrote] {summary_txt_path}", flush=True)
    print()
    print("\n".join(txt_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

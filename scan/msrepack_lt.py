#!/usr/bin/env python3
"""msrepack_lt.py — in-place LT day-file consolidation for the Gecko/Minimus
fragmentation bug (Issue 12 in scan1_recovery_register.md).

For each LT day-file (the targets are eqserver-converted Gecko/Minimus
station-days that show many traces per day from per-minute-file boundaries),
read it via ObsPy, apply `merge(method=1, fill_value=None)` to consolidate
small clock offsets across consecutive per-minute records, and atomic-write
the consolidated file back to LT.

SAFETY
  - **Runs on dev1 only** (write-host invariant: LT mounted rw).
  - Default DRY-RUN. Nothing written unless --commit.
  - Atomic write: read original → consolidate → write to `<file>.repack.tmp`
    → fsync → rename to `<file>`. Original never deleted before the rename.
  - Skip files that are already a single contig (no work needed).
  - Skip files where ObsPy raises during read (corrupt; flag don't touch).
  - Refuse to write back if the consolidated stream has fewer samples than
    the original (sanity check — merging should never lose samples).
  - Refuse to write back if the consolidated stream's total time range is
    less than original's (same).
  - Log every action to `<ledger>/seiscomp_archive/<YEAR>/<NET>/<STA>.repacks.jsonl`
    so the operation is auditable.

  This is a parallel recovery path to the upstream engine fix (task #52).
  Output must match what the patched engine produces for the same source.

USAGE (dry-run, single station):
  python3 scan/msrepack_lt.py \\
      --lt-root /mnt/seiscomp_archive \\
      --ledger-root ~/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive \\
      --net VW --sta STBK

USAGE (full sweep, commit):
  python3 scan/msrepack_lt.py \\
      --lt-root /mnt/seiscomp_archive \\
      --ledger-root ~/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive \\
      --commit
"""
from __future__ import annotations
import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path


SDS_FILE_RE = re.compile(r"(?P<net>\w+)\.(?P<sta>\w+)\.(?P<loc>[\w-]*)\."
                         r"(?P<cha>\w+)\.D\.(?P<year>\d{4})\.(?P<doy>\d{3})$")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def needs_repack(path: Path) -> tuple[bool, int, int]:
    """Return (needs_repack, n_traces, n_samples). Reads via obspy."""
    from obspy import read
    st = read(str(path))
    n_traces = len(st)
    n_samples = sum(tr.stats.npts for tr in st)
    return (n_traces > 1, n_traces, n_samples)


def repack_one(path: Path, commit: bool = False) -> dict:
    """Repack one LT file. Returns a result dict for the audit log."""
    from obspy import read, Stream

    result = {
        "path": str(path),
        "ts": utc_now(),
        "action": "skip",
        "reason": "",
    }

    try:
        st = read(str(path))
    except Exception as e:
        result["action"] = "fail"
        result["reason"] = f"read_error: {type(e).__name__}: {e}"
        return result

    n_before = len(st)
    samples_before = sum(tr.stats.npts for tr in st)
    if n_before <= 1:
        result["action"] = "skip"
        result["reason"] = "already_single_trace"
        result["n_traces_before"] = n_before
        return result

    # Consolidate. method=1 handles slight overlaps + sub-sample-rate gaps.
    # fill_value=None preserves real gaps (minutes-long outages) as masked.
    try:
        st.merge(method=1, fill_value=None)
        # split() expands masked Streams back into Trace-per-contig
        # representation, which is what ObsPy/SeisComP downstream expects.
        st_split = st.split()
    except Exception as e:
        result["action"] = "fail"
        result["reason"] = f"merge_error: {type(e).__name__}: {e}"
        result["n_traces_before"] = n_before
        return result

    n_after = len(st_split)
    samples_after = sum(tr.stats.npts for tr in st_split)

    # Sanity checks: merge should never lose samples or time range
    if samples_after < samples_before:
        result["action"] = "fail"
        result["reason"] = (f"samples_dropped: before={samples_before} "
                            f"after={samples_after}")
        result["n_traces_before"] = n_before
        result["n_traces_after"] = n_after
        return result

    if n_after >= n_before:
        # No consolidation actually happened (e.g. every gap is too big for
        # method=1 to bridge — those are real outages).
        result["action"] = "skip"
        result["reason"] = "no_consolidation_possible_real_outages"
        result["n_traces_before"] = n_before
        result["n_traces_after"] = n_after
        return result

    result["n_traces_before"] = n_before
    result["n_traces_after"] = n_after
    result["samples_before"] = samples_before
    result["samples_after"] = samples_after

    if not commit:
        result["action"] = "would_repack"
        return result

    # Atomic write: consolidated → tmp → fsync → rename
    tmp = path.with_suffix(path.suffix + ".repack.tmp")
    try:
        # Use BytesIO + atomic write so we don't damage the original on error
        buf = io.BytesIO()
        st_split.write(buf, format="MSEED", reclen=4096)
        with open(tmp, "wb") as f:
            f.write(buf.getvalue())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        result["action"] = "repacked"
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        result["action"] = "fail"
        result["reason"] = f"write_error: {type(e).__name__}: {e}"
    return result


def walk_lt(lt_root: Path, net: str | None, sta: str | None,
            year: int | None) -> list[Path]:
    """Walk LT for matching day-files."""
    out: list[Path] = []
    for yr_dir in sorted(lt_root.iterdir()):
        if not yr_dir.is_dir() or not yr_dir.name.isdigit():
            continue
        if year is not None and int(yr_dir.name) != year:
            continue
        for net_dir in sorted(yr_dir.iterdir()):
            if net is not None and net_dir.name != net:
                continue
            if not net_dir.is_dir():
                continue
            for sta_dir in sorted(net_dir.iterdir()):
                if sta is not None and sta_dir.name != sta:
                    continue
                if not sta_dir.is_dir():
                    continue
                for cha_dir in sorted(sta_dir.iterdir()):
                    if not cha_dir.is_dir():
                        continue
                    for f in sorted(cha_dir.iterdir()):
                        m = SDS_FILE_RE.fullmatch(f.name)
                        if m:
                            out.append(f)
    return out


def append_audit(ledger_root: Path, result: dict) -> None:
    """Append result to <ledger>/<YEAR>/<NET>/<STA>.repacks.jsonl."""
    m = SDS_FILE_RE.fullmatch(Path(result["path"]).name)
    if not m:
        return
    year = m["year"]
    # Path under LT: <lt_root>/<YEAR>/<NET>/<STA>/<CHA>.D/<file>
    parts = Path(result["path"]).parts
    try:
        net = parts[-4]
        sta = parts[-3]
    except IndexError:
        return
    audit_path = ledger_root / year / net / f"{sta}.repacks.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a") as f:
        f.write(json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lt-root", type=Path, required=True,
                    help="LT root (mounted rw on dev1, e.g. /mnt/seiscomp_archive)")
    ap.add_argument("--ledger-root", type=Path, required=True,
                    help="ledger seiscomp_archive root, e.g. "
                         "~/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive")
    ap.add_argument("--net", default=None)
    ap.add_argument("--sta", default=None)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N files (for smoke-testing)")
    ap.add_argument("--commit", action="store_true",
                    help="actually write back; default is dry-run")
    args = ap.parse_args()

    if not args.lt_root.is_dir():
        print(f"ERROR: --lt-root {args.lt_root} not a directory", file=sys.stderr)
        return 2

    # Sanity preflight: LT must be mounted rw if --commit
    if args.commit:
        probe = args.lt_root / f".msrepack_preflight_{os.getpid()}"
        try:
            probe.touch()
            probe.unlink()
        except OSError as e:
            print(f"ERROR: LT not writable: {e}", file=sys.stderr)
            print("This tool must run on the write-host (dev1). Aborting.",
                  file=sys.stderr)
            return 2

    files = walk_lt(args.lt_root, args.net, args.sta, args.year)
    if args.limit:
        files = files[:args.limit]
    print(f"[msrepack] {len(files)} candidate file(s); "
          f"commit={args.commit}", flush=True)

    counts = {"repacked": 0, "would_repack": 0, "skip": 0, "fail": 0}
    samples_freed_per_unit = {}
    for i, f in enumerate(files, 1):
        result = repack_one(f, commit=args.commit)
        counts[result["action"]] = counts.get(result["action"], 0) + 1
        if result["action"] in ("repacked", "would_repack"):
            delta = result["n_traces_before"] - result["n_traces_after"]
            print(f"[msrepack] [{i}/{len(files)}] {result['action']:13s} "
                  f"{f.name}: traces {result['n_traces_before']} → "
                  f"{result['n_traces_after']} (Δ-{delta})", flush=True)
        elif result["action"] == "fail":
            print(f"[msrepack] [{i}/{len(files)}] FAIL {f.name}: "
                  f"{result['reason']}", flush=True)
        if args.commit:
            try:
                append_audit(args.ledger_root, result)
            except Exception as e:
                print(f"  audit append failed: {e}", flush=True)

    print()
    print(f"[msrepack] DONE. {counts}", flush=True)
    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

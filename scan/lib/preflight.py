#!/usr/bin/env python3
"""preflight — per-stage repo pinning check (CLAUDE.md commit-hash discipline).

Before a stage runs, every repo that stage depends on must be at a known,
committed SHA: no uncommitted changes, and — when a pin is set in
config/pinned_repo_shas.yaml — HEAD equal to the pin. This is the guard
whose absence allowed the 2026-06 side-load contamination incident.

Library use (orchestrators call this and embed the result in the run
manifest):

    from lib.preflight import run_preflight, PreflightError
    record = run_preflight("convert")      # raises PreflightError on fail
    manifest["preflight"] = record

CLI use (operator pre-check):

    python3 scan/lib/preflight.py --stage convert
    python3 scan/lib/preflight.py --stage convert --json

Exit code 0 = stage may run; 1 = abort (reason on stderr).

Repos are resolved as siblings of this repository's checkout (../<name>),
which holds on both the Mac and the staging VM.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "pinned_repo_shas.yaml"


class PreflightError(RuntimeError):
    """A stage dependency is dirty, missing, or off its pin."""


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise PreflightError(f"git {' '.join(args)} failed in {repo}: "
                             f"{p.stderr.strip()}")
    return p.stdout.strip()


# Untracked files abort only when they are executable code — the side-load
# class. Untracked data/notes (xlsx, notebooks, .claude/) are recorded as
# warnings, not failures. Any modification to a TRACKED file always aborts.
UNTRACKED_CODE_SUFFIXES = (".py", ".sh", ".bash")


def check_repo(name: str, pinned: str | None) -> dict:
    """One repo: exists, clean (no tracked modifications, no untracked
    code), and on its pin (when set)."""
    repo = REPO_ROOT if name == REPO_ROOT.name else REPO_ROOT.parent / name
    if not (repo / ".git").exists():
        raise PreflightError(f"{name}: repo not found at {repo}")
    head = _git(repo, "rev-parse", "HEAD")
    tracked_dirty, untracked_code, untracked_other = [], [], []
    for line in _git(repo, "status", "--porcelain").splitlines():
        path = line[3:]
        if line.startswith("??"):
            if path.rstrip("/").endswith(UNTRACKED_CODE_SUFFIXES):
                untracked_code.append(path)
            else:
                untracked_other.append(path)
        else:
            tracked_dirty.append(line)
    if tracked_dirty:
        raise PreflightError(
            f"{name}: {len(tracked_dirty)} modified tracked file(s) — commit "
            f"or stash before any stage run (first: {tracked_dirty[0]})")
    if untracked_code:
        raise PreflightError(
            f"{name}: untracked code present (side-load risk): "
            f"{untracked_code[:3]}{'...' if len(untracked_code) > 3 else ''}")
    if pinned and not head.startswith(pinned):
        raise PreflightError(
            f"{name}: HEAD {head[:12]} != pinned {pinned} "
            f"(config/pinned_repo_shas.yaml)")
    return {"repo": name, "head": head, "pinned": pinned,
            "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "untracked_ignored": untracked_other[:20]}


def run_preflight(stage: str, config_path: Path = CONFIG_PATH) -> dict:
    """Check every repo the stage depends on. Returns the record to embed
    in the stage's run manifest; raises PreflightError on any failure."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    stages = cfg.get("stages") or {}
    if stage not in stages:
        raise PreflightError(
            f"unknown stage '{stage}' (known: {sorted(stages)})")
    repos_cfg = cfg.get("repos") or {}
    checks = []
    for name in stages[stage]:
        pinned = (repos_cfg.get(name) or {}).get("pinned")
        checks.append(check_repo(name, pinned))
    return {
        "stage": stage,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": str(config_path.relative_to(REPO_ROOT)),
        "repos": checks,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", required=True)
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--json", action="store_true",
                    help="print the manifest record as JSON")
    args = ap.parse_args()
    try:
        record = run_preflight(args.stage, Path(args.config))
    except PreflightError as exc:
        print(f"PREFLIGHT ABORT [{args.stage}]: {exc}", file=sys.stderr)
        sys.exit(1)
    if args.json:
        json.dump(record, sys.stdout, indent=2)
        print()
    else:
        print(f"preflight OK [{record['stage']}] @ {record['ts']}")
        for c in record["repos"]:
            pin = f" (pin {c['pinned']})" if c["pinned"] else " (unpinned)"
            print(f"  {c['repo']:22s} {c['head'][:12]} [{c['branch']}]{pin}")


if __name__ == "__main__":
    main()

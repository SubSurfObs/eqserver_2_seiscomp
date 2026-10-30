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


# Untracked files abort only when they are executable code INSIDE the
# repo's declared code roots — the side-load class. Untracked data/notes
# (xlsx, notebooks, .claude/) and scratch code OUTSIDE the code roots are
# recorded as warnings, not failures (review 2026-10-23 F8: an unrelated
# scratch .py must not block forever, but a side-load in an import path
# must never slip through). Any modification to a TRACKED file always
# aborts.
UNTRACKED_CODE_SUFFIXES = (".py", ".sh", ".bash")


def _unquote(path: str) -> str:
    """git quotes paths containing spaces/escapes; undo for suffix tests."""
    if path.startswith('"') and path.endswith('"'):
        try:
            return (path[1:-1].encode("utf-8").decode("unicode_escape")
                    .encode("latin-1").decode("utf-8", "replace"))
        except Exception:
            return path[1:-1]
    return path


def check_repo(name: str, pinned: str | None,
               code_roots: list | None = None,
               strict: bool = False) -> dict:
    """One repo: exists, clean (no tracked modifications, no untracked
    code in its code roots), and on its pin (when set; strict requires a
    pin to BE set — production mode)."""
    # The MAIN repo is always the checkout this very file executes from,
    # regardless of its directory name (Codex fix-review F8: the VM clone
    # is named eqserver_2_seiscomp_v3, and matching by basename made the
    # preflight validate the old sibling checkout instead of the code
    # actually running). Siblings resolve by name as before.
    MAIN_REPO = "eqserver_2_seiscomp"
    if name == MAIN_REPO or name == REPO_ROOT.name:
        repo = REPO_ROOT
    else:
        repo = REPO_ROOT.parent / name
    if not (repo / ".git").exists():
        raise PreflightError(f"{name}: repo not found at {repo}")
    head = _git(repo, "rev-parse", "HEAD")
    tracked_dirty, untracked_code, untracked_other = [], [], []
    # --untracked-files=all: list every file individually; the default
    # collapses a new directory to '?? dir/', hiding any code inside it
    # (review 2026-10-23 F8 — the nested side-load blind spot).
    for line in _git(repo, "status", "--porcelain",
                     "--untracked-files=all").splitlines():
        path = _unquote(line[3:])
        if line.startswith("??"):
            if path.endswith(UNTRACKED_CODE_SUFFIXES):
                in_roots = code_roots is None or any(
                    path == r or path.startswith(r.rstrip("/") + "/")
                    for r in code_roots)
                (untracked_code if in_roots else untracked_other).append(path)
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
            f"{name}: untracked code in import/entry paths (side-load risk): "
            f"{untracked_code[:3]}{'...' if len(untracked_code) > 3 else ''}")
    if strict and not pinned:
        raise PreflightError(
            f"{name}: no pin set in config/pinned_repo_shas.yaml — "
            f"production runs require every stage repo pinned to an exact "
            f"SHA (operator decision; see the OWED DECISION note in that "
            f"file)")
    if pinned and not head.startswith(pinned):
        # The main repo cannot contain its own SHA: recording the pin is
        # itself a commit. Accept HEAD when it descends from the pin and
        # the ONLY files changed since are the pin config itself.
        ok_pin_only = False
        if repo == REPO_ROOT:
            try:
                _git(repo, "merge-base", "--is-ancestor", pinned, "HEAD")
                changed = [f for f in _git(repo, "diff", "--name-only",
                                           pinned, "HEAD").splitlines() if f]
                ok_pin_only = changed == ["config/pinned_repo_shas.yaml"]
            except PreflightError:
                ok_pin_only = False
        if not ok_pin_only:
            raise PreflightError(
                f"{name}: HEAD {head[:12]} != pinned {pinned} "
                f"(config/pinned_repo_shas.yaml)")
    return {"repo": name, "head": head, "pinned": pinned,
            "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
            "untracked_ignored": untracked_other[:20]}


def run_preflight(stage: str, config_path: Path = CONFIG_PATH,
                  strict: bool = False) -> dict:
    """Check every repo the stage depends on. Returns the record to embed
    in the stage's run manifest; raises PreflightError on any failure.
    strict=True (production): every repo must additionally have a non-null
    pin."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    stages = cfg.get("stages") or {}
    if stage not in stages:
        raise PreflightError(
            f"unknown stage '{stage}' (known: {sorted(stages)})")
    repos_cfg = cfg.get("repos") or {}
    checks = []
    for name in stages[stage]:
        rc = repos_cfg.get(name) or {}
        checks.append(check_repo(name, rc.get("pinned"),
                                 rc.get("code_roots"), strict))
    return {
        "stage": stage,
        "strict": strict,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": str(config_path.relative_to(REPO_ROOT)),
        "repos": checks,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", required=True)
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--strict", action="store_true",
                    help="production mode: every repo must have a non-null "
                         "pin (the mode run_production_convert.py uses)")
    ap.add_argument("--json", action="store_true",
                    help="print the manifest record as JSON")
    args = ap.parse_args()
    try:
        record = run_preflight(args.stage, Path(args.config), args.strict)
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

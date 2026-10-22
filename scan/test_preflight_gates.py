#!/usr/bin/env python3
"""test_preflight_gates — the F8 hardening (review 2026-10-23).

Proves: a side-loaded script nested in a NEW untracked directory inside a
code root aborts (the '?? dir/' collapse blind spot); scratch code outside
the code roots only warns; a quoted path (space in filename) is still
caught; strict mode refuses a null pin. Stdlib + pyyaml only; builds
throwaway git repos in a temp dir.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib.preflight as pf  # noqa: E402

FAILURES = []


def check(name, fn, want_error_snippet=None):
    try:
        fn()
        ok = want_error_snippet is None
        got = "no error"
    except pf.PreflightError as e:
        ok = want_error_snippet is not None and want_error_snippet in str(e)
        got = str(e)
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILURES.append(name)
        print(f"      got: {got[:160]}")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin:/usr/local/bin"})


def make_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True,
                   capture_output=True)
    (path / "scan").mkdir()
    (path / "scan" / "ok.py").write_text("# committed\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        repo = make_repo(td / "mainrepo")
        old_root = pf.REPO_ROOT
        pf.REPO_ROOT = repo
        try:
            roots = ["scan"]

            check("clean repo, null pin, non-strict passes",
                  lambda: pf.check_repo("mainrepo", None, roots, False))

            check("strict refuses null pin",
                  lambda: pf.check_repo("mainrepo", None, roots, True),
                  "no pin set")

            # Side-load nested in a NEW directory inside a code root: the
            # default porcelain output collapses this to '?? scan/local/',
            # which the old suffix test missed.
            (repo / "scan" / "local").mkdir()
            (repo / "scan" / "local" / "suds_convert.py").write_text("x\n")
            check("nested side-load in code root aborts",
                  lambda: pf.check_repo("mainrepo", None, roots, False),
                  "side-load risk")
            (repo / "scan" / "local" / "suds_convert.py").unlink()
            (repo / "scan" / "local").rmdir()

            # Scratch code OUTSIDE the code roots: warning, not an abort.
            (repo / "notebooks").mkdir()
            (repo / "notebooks" / "scratch.py").write_text("x\n")
            check("scratch code outside roots only warns",
                  lambda: pf.check_repo("mainrepo", None, roots, False))

            # Quoted path (space in the name) inside a code root.
            (repo / "scan" / "side load.py").write_text("x\n")
            check("quoted spaced path in code root aborts",
                  lambda: pf.check_repo("mainrepo", None, roots, False),
                  "side-load risk")
            (repo / "scan" / "side load.py").unlink()

            # Modified tracked file always aborts.
            (repo / "scan" / "ok.py").write_text("# changed\n")
            check("modified tracked file aborts",
                  lambda: pf.check_repo("mainrepo", None, roots, False),
                  "modified tracked")
        finally:
            pf.REPO_ROOT = old_root

    if FAILURES:
        print(f"\n{len(FAILURES)} preflight test(s) FAILED: {FAILURES}")
        return 1
    print("\nall preflight-gate tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""triage_study — sampled proof that triage achieves maximum channel coverage.

The question (operator, 2026-10-19): given a messy EqServer day directory,
does the v3 triage + converter get every recoverable channel into the
output (one miniSEED file per channel per day)? Tested by STRATIFIED
SAMPLING across VW, VX and DU, weighted to the day-types where coverage
can actually be lost:

  multi_copy    both disk and telemetry copies exist for the day
  suffixed      day contains single-channel-named files
  bundle        day contains hourly-bundle files
  unparsed      day contains files the scanner could not key by minute
  mixed_rec     more than one recorder type in the day
  clean         control: single-population days

Per sampled day: compile (or reuse) the station-year selection with the
v0.2 triage → convert the day in a sandbox via phase3 --selection →
audit EXHAUSTIVELY (every copy of every minute read: QA_PROBE_EVERY=1,
QA_EXTRA_CAP unlimited) against the origin archive. PASS = zero sample
mismatches AND union-coverage not in output ≈ 0 (missing_s + sel_loss_s
below tolerance). Results stream to results.jsonl (resume-by-default).

Writes only under its --out-dir (sandbox SDS + selections + results).
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.preflight import REPO_ROOT  # noqa: E402

PY = sys.executable
PASS_TOL_S = 2.0  # union seconds absent from output tolerated per channel-day

STRATA_SQL = {
    "multi_copy": "SELECT 1 FROM files WHERE role='waveform' AND station_mismatch=0 "
                  "AND file_year=? AND file_month=? AND file_day=? AND source_type='disk' LIMIT 1",
    # evaluated in python below; SQL above used as disk-presence probe
}


def day_strata(db, y, m, d):
    rows = list(db.execute(
        "SELECT source_type, channel_suffix, hhmm, flags, recorder_type, exclude_reason "
        "FROM files WHERE role='waveform' AND station_mismatch=0 "
        "AND file_year=? AND file_month=? AND file_day=?", (y, m, d)))
    if not rows:
        return set()
    live = [r for r in rows if r[5] is None or r[5] in ("single_channel", "unparsed")]
    if not live:
        return set()
    s = set()
    srcs = {r[0] for r in live}
    if "disk" in srcs and "telemetry" in srcs:
        s.add("multi_copy")
    if any(r[1] for r in live):
        s.add("suffixed")
    if any(r[3] and "hour_bundle" in r[3] for r in live) or any(
            r[2] is None and r[5] is None and r[0] == "telemetry" for r in live):
        s.add("bundle_or_unparsed")
    if len({r[4] for r in live}) > 1:
        s.add("mixed_rec")
    if not s:
        s.add("clean")
    return s


def build_sample(db_dirs, networks, n_per, seed, out_path):
    import random
    rng = random.Random(seed)
    buckets = {}
    for db_dir in db_dirs:
        for p in sorted(Path(db_dir).expanduser().glob("*.db")):
            net, sta = p.stem.split(".", 1)
            if net not in networks:
                continue
            try:
                db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                days = list(db.execute(
                    "SELECT DISTINCT file_year, file_month, file_day FROM files "
                    "WHERE role='waveform' AND station_mismatch=0 "
                    "AND file_year BETWEEN 1995 AND 2026"))
                # cap per-station day scan cost: sample candidate days first.
                # HONESTY LABEL (fix-review F10): strata are built from this
                # bounded random pre-sample (400 shuffled days per station),
                # NOT an exhaustive index scan — a rare mode confined to a
                # station's other days can be missed. Exhaustive stratum
                # predicates are a tracked follow-up.
                rng.shuffle(days)
                for y, m, d in days[:400]:
                    for st in day_strata(db, y, m, d):
                        buckets.setdefault((net, st), []).append(
                            {"net": net, "sta": sta, "db": str(p),
                             "date": f"{y:04d}-{m:02d}-{d:02d}", "stratum": st})
                db.close()
            except Exception as e:
                print(f"WARN {p.name}: {e}", flush=True)
    sample = []
    seen = set()
    for (net, st), items in sorted(buckets.items()):
        rng.shuffle(items)
        for it in items[:n_per]:
            k = (it["net"], it["sta"], it["date"])
            if k not in seen:
                seen.add(k)
                sample.append(it)
    rng.shuffle(sample)
    with open(out_path, "w") as f:
        for it in sample:
            f.write(json.dumps(it) + "\n")
    counts = {}
    for it in sample:
        counts[f'{it["net"]}:{it["stratum"]}'] = counts.get(
            f'{it["net"]}:{it["stratum"]}', 0) + 1
    print("sample:", json.dumps(counts, indent=0, sort_keys=True), flush=True)
    return sample


def _synth_plan(db_path, net, sta, registry, out_path: Path):
    """Study-only minimal plan: per-year majority recorder epochs, status ok.
    Real conversions use reviewed identity; this exists so phase3 can
    dispatch engines for stations that never had a v1 plan (DU/VX).
    Mixed-recorder days get the year's majority engine — a known
    limitation the mixed_rec stratum is there to expose."""
    import yaml
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # No exclude_reason filter (Codex item 5): the study deliberately
    # REVIVES single_channel/unparsed cohorts as candidates, so the synth
    # plan's epochs must cover the years those files span — filtering them
    # out left 12 DU stations with no epochs at all and dates outside every
    # epoch, which converted nothing and FAILed as whole missing days.
    rows = list(db.execute(
        "SELECT file_year, recorder_type, COUNT(*) FROM files "
        "WHERE role='waveform' AND station_mismatch=0 "
        "AND file_year BETWEEN 1995 AND 2026 GROUP BY 1,2"))
    db.close()
    by_year = {}
    for y, rec, n in rows:
        cur = by_year.get(y)
        if cur is None or n > cur[1]:
            by_year[y] = (rec, n)
    entry = registry.get(sta) or {}
    rts = entry.get("recorder_types") or []
    epochs = []
    for i, (y, (rec, _n)) in enumerate(sorted(by_year.items())):
        eff = {"echopro": "echopro", "gecko": "gecko"}.get(rec, None)
        if eff is None:
            eff = "minimus" if "minimus" in rts else "gecko"
        epochs.append({"id": i + 1, "start": f"{y}-01-01", "end": f"{y}-12-31",
                       "recorder": eff, "days": 0, "classifications": {}})
    plan = {"station": sta,
            "network": entry.get("target_network") or net,
            "location": entry.get("target_location") or "00",
            "status": "ok", "epochs": epochs, "flagged_days": [],
            "synthesised_for": "triage_study (NOT a reviewed plan)"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(plan, sort_keys=False))
    return out_path


def _repo_sha(repo: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              timeout=10).stdout.strip() or "?"
    except Exception:
        return "?"


def _repo_dirty(repo: Path) -> bool:
    try:
        return bool(subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:
        return True


def run_study(sample, out_dir: Path, repo_root: Path, max_hours: float):
    results_path = out_dir / "results.jsonl"
    sel_dir = out_dir / "selections"
    sds_dir = out_dir / "sds"
    sel_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QA_PROBE_EVERY="1", QA_EXTRA_CAP="999999")
    deadline = time.time() + max_hours * 3600
    # Codex fix-review F10: every result is bound to the code that produced
    # it, a dirty tree is marked as such (its SHA describes nothing), and
    # resume treats a prior PASS/FAIL as terminal ONLY when it was produced
    # by the SAME three code identities — restarting after a code change
    # re-runs everything instead of mixing versions.
    code_id = {
        "eqserver_sha": _repo_sha(repo_root)
        + ("+dirty" if _repo_dirty(repo_root) else ""),
        "sudspy_sha": _repo_sha(repo_root.parent / "sudspy")
        + ("+dirty" if _repo_dirty(repo_root.parent / "sudspy") else ""),
        "disk_to_sds_sha": _repo_sha(repo_root.parent / "disk_to_sds")
        + ("+dirty" if _repo_dirty(repo_root.parent / "disk_to_sds") else ""),
    }
    done = set()
    if results_path.exists():
        for line in open(results_path):
            try:
                r = json.loads(line)
                if (r.get("verdict") in ("PASS", "FAIL", "BLOCKED")
                        and all(r.get(k) == v for k, v in code_id.items())):
                    done.add((r["net"], r["sta"], r["date"]))
            except Exception:
                pass
    out = open(results_path, "a")
    n = 0
    for it in sample:
        if (it["net"], it["sta"], it["date"]) in done:
            continue
        if time.time() > deadline:
            print("max-hours reached; resume to continue", flush=True)
            break
        year = it["date"][:4]
        sel = sel_dir / f'{it["net"]}.{it["sta"]}.{year}.selection.sqlite'
        rec = dict(it, ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   **code_id)
        t0 = time.time()
        day_sds = None  # assigned after selection compile; retention guards on it
        try:
            # Review F10: a cached selection compiled by a DIFFERENT chooser
            # version is stale evidence — recompile it.
            if sel.exists():
                try:
                    sc = sqlite3.connect(f"file:{sel}?mode=ro", uri=True)
                    gsha = dict(sc.execute(
                        "SELECT key, value FROM meta")).get("generator_sha")
                    sc.close()
                except Exception:
                    gsha = None
                if gsha != code_id["eqserver_sha"]:
                    sel.unlink()
            if not sel.exists():
                r = subprocess.run(
                    [PY, "scan/coverage_planner.py", "--net", it["net"],
                     "--sta", it["sta"], "--year", year, "--db", it["db"],
                     "--out", str(sel)],
                    capture_output=True, text=True, cwd=repo_root, timeout=3600)
                if r.returncode != 0:
                    rec.update(phase="triage", status="error",
                               error=r.stderr.strip()[-300:])
                    raise StopIteration
            dates_f = out_dir / "dates.tmp"
            dates_f.write_text(it["date"] + "\n")
            day_sds = sds_dir / it["net"] / it["sta"] / it["date"]
            plan = repo_root / "plans" / it["net"] / f'{it["net"]}.{it["sta"]}.plan.yaml'
            if not plan.exists():
                import yaml as _y
                with open(repo_root / "metadata" / "station_registry.yaml") as _f:
                    _reg = _y.safe_load(_f) or {}
                plan = _synth_plan(it["db"], it["net"], it["sta"], _reg,
                                   out_dir / "synth_plans" /
                                   f'{it["net"]}.{it["sta"]}.plan.yaml')
            cmd = [PY, "scan/phase3_driver.py", it["db"], str(plan),
                   "--registry", "metadata/station_registry.yaml",
                   "--staging-sds", str(day_sds), "--commit",
                   "--dates-file", str(dates_f), "--workers", "1",
                   "--selection", str(sel)]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               cwd=repo_root, timeout=1800)
            if r.returncode != 0 or "SELECTION REFUSED" in r.stdout:
                out_text = (r.stdout + r.stderr).strip()
                # probe-blocked is a TERMINAL verdict, not an error to
                # retry: the chooser established that nobody can prove the
                # population's channels — operator identity work. KEEP the
                # selection (its probes table IS the evidence); deleting it
                # both destroyed the evidence and made every retry re-probe
                # the month (observed 2026-10-26).
                if "probe-blocked" in out_text:
                    rec.update(phase="convert", status="done",
                               verdict="BLOCKED",
                               error=out_text[-300:])
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    n += 1
                    continue
                rec.update(phase="convert", status="error",
                           error=out_text[-300:])
                # Any OTHER refused selection is stale evidence (identity
                # drift, schema change): drop it so the retry recompiles.
                if "SELECTION REFUSED" in r.stdout:
                    sel.unlink(missing_ok=True)
                raise StopIteration
            if "blocked_identity" in r.stdout:
                # Per-day identity block (Codex item 3 rescope): terminal
                # verdict; the day is operator work, not a retry.
                tail_line = [ln for ln in r.stdout.splitlines()
                             if "blocked_identity" in ln]
                rec.update(phase="convert", status="done", verdict="BLOCKED",
                           error=(tail_line[-1] if tail_line else "")[-300:])
                out.write(json.dumps(rec) + "\n")
                out.flush()
                n += 1
                continue
            r = subprocess.run(
                [PY, "scan/qa_verify.py", "--net", it["net"], "--sta", it["sta"],
                 "--date", it["date"], "--sds-root", str(day_sds),
                 "--selection", str(sel),
                 "--db-dirs", str(Path(it["db"]).parent)],
                capture_output=True, text=True, cwd=repo_root, env=env,
                timeout=3600)
            try:
                import hashlib as _hl
                rec["selection_sha"] = _hl.sha256(
                    sel.read_bytes()).hexdigest()[:16]
            except OSError:
                pass
            audit = json.loads(r.stdout)
            per_chan = audit.get("per_chan") or {}
            miss = sel_loss = bad = 0.0
            worst_chan_s = 0.0
            for ch, v in per_chan.items():
                miss += v.get("missing_s", 0)
                sel_loss += v.get("sel_loss_s", 0)
                bad += v.get("n_bad", 0)
                worst_chan_s = max(worst_chan_s,
                                   v.get("missing_s", 0) + v.get("sel_loss_s", 0))
            # Review 2026-10-23 F10: PASS requires a USABLE audit. Finding
            # categories are classified fail-closed: audit-infrastructure
            # failures => ERROR (retried); any category that is not the
            # numeric missing/selection-loss pair (already measured by the
            # per-channel bound) => FAIL, including categories this code
            # does not know.
            cats = {}
            for f in (audit.get("findings") or []):
                c = f.get("category", "unknown")
                cats[c] = cats.get(c, 0) + 1
            AUDIT_ERROR_CATS = {"comparator_error", "no_station_db"}
            QUANTIFIED_CATS = {"missing_channel", "selection_loss"}
            hard_fail = sorted(c for c in cats
                               if c not in AUDIT_ERROR_CATS
                               and c not in QUANTIFIED_CATS)
            rec.update(phase="audit",
                       missing_s=round(miss, 2), sel_loss_s=round(sel_loss, 2),
                       n_bad=int(bad),
                       union_gap_s=round(miss + sel_loss, 2),
                       n_chans=len(per_chan), finding_cats=cats)
            if (cats.keys() & AUDIT_ERROR_CATS) or not per_chan:
                rec.update(status="error",
                           error=f"audit unusable: cats={sorted(cats)} "
                                 f"n_chans={len(per_chan)}")
                raise StopIteration
            # Fix-review F10: the loss bound applies PER CHANNEL — one
            # channel may not spend the whole multi-channel allowance.
            rec.update(status="done", worst_chan_s=round(worst_chan_s, 2),
                       verdict="PASS" if (not hard_fail and bad == 0
                                          and worst_chan_s <= PASS_TOL_S)
                               else "FAIL")
        except StopIteration:
            rec["verdict"] = "ERROR"
        except Exception as e:
            rec.update(status="error", error=f"{type(e).__name__}: {e}",
                       verdict="ERROR")
        rec["elapsed_s"] = round(time.time() - t0, 1)
        out.write(json.dumps(rec) + "\n")
        out.flush()
        n += 1
        if n % 10 == 0:
            print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                  f"{n} days done", flush=True)
        # Keep the sandbox small — but RETAIN the converted output of every
        # non-PASS day (Codex item 6: distinguishing converter trimming
        # from auditor arithmetic needs the actual SDS bytes). PASS days
        # are deleted as before.
        import shutil
        if rec.get("verdict") in ("FAIL", "ERROR") and day_sds is not None:
            keep = out_dir / "failed_sds" / f'{it["net"]}.{it["sta"]}.{it["date"]}'
            try:
                if day_sds.exists():
                    shutil.rmtree(keep, ignore_errors=True)
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(day_sds), str(keep))
            except OSError:
                pass
        shutil.rmtree(sds_dir, ignore_errors=True)
    out.close()
    print(f"study pass complete: {n} new days")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-dirs", nargs="+", required=True)
    ap.add_argument("--networks", nargs="+", default=["VW", "VX", "DU"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n-per-stratum", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--max-hours", type=float, default=36.0)
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_path = out_dir / "sample.jsonl"
    if sample_path.exists():
        sample = [json.loads(l) for l in open(sample_path)]
        print(f"resuming with existing sample ({len(sample)} days)", flush=True)
    else:
        sample = build_sample(args.db_dirs, set(args.networks),
                              args.n_per_stratum, args.seed, sample_path)
    run_study(sample, out_dir, REPO_ROOT, args.max_hours)


if __name__ == "__main__":
    main()

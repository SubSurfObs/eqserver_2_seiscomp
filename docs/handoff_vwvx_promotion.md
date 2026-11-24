# Handoff — promoting the VW/VX re-sweep (release v3.0)

For `seiscomp_server_uom`. Written 2026-11-24 by the conversion session.
Codex: plan `reviews/2026-11-18_vwvx_resweep_plan.md` (F1, F2),
release `reviews/2026-11-24_vwvx_release_v30.md`.

## What is different from DU

VW and most VX station-years are **already in LT** (v1 pipeline, June 2026,
plus live SeedLink for recent years). Promotion therefore mostly decides
between an existing LT day-file and a new staged one. `apply.py`'s
sample-count rule is not enough for that (Codex F1): an LT day can have the
right count with wrong timing or values, and a higher staged count does not
prove staging contains everything LT has.

## The rule

Before promoting a station-year, run the comparison (read-only, on the
staging VM or dev1):

    python3 scan/compare_lt.py --staging-root /mnt/seiscomp_staging/seiscomp_archive \
        --lt-root /mnt/seiscomp_archive --net VW --sta LOCU --year 2016 \
        --workers 6 --out <queue>/compare/VW.LOCU.2016.jsonl

One verdict per staged day-file, by exact sample time and value, each bound to
the sha256 of the staged and the LT file it was computed from:

| verdict | action |
|---|---|
| `write` (no LT file) | write |
| `identical` | skip |
| `superset` (staging holds every LT sample, same time and value, plus more) | replace: LT file renamed (`.pre-eqserver-v3`), never deleted; record the decision |
| `conflict` (an LT sample missing from staging, or a differing value) | **hold** for review |
| `unreadable` | **hold** |

Before writing or replacing, re-hash the staged file and the LT file and
require both to equal the hashes in the verdict. If either changed, rerun the
comparison. Never act on a verdict for different bytes.

**2025–26 (live SeedLink years, Codex F2):** existing live LT files are
authoritative by default. Only `superset` may replace them; `conflict` is held
and the staged copy kept for a later explicit union. Never decide by raw count.

## Queues

`/mnt/seiscomp_staging/eqserver_sweep_vwvx_v3_r0` (VX) and `_r1`, `_r2`, `_r3`
(VW, station-disjoint). Same `convert_done` / accepted-by-ruling record format
as DU (`staging_root`, `run_manifest_path` present). Promote only after Codex
has spot-checked the queue.

## Clean-up rulings (Dan) to apply after the re-sweep copy is in

Retire, renamed and never deleted: DL\* (BRIG 2020, LOCU 2021), SUM (BRTH,
LRNW, LRSH, LRWS 2021), CDO (WLSH/WDSD 2018, HOGN 2019: noise-only fourth
channel), HQ\*/CQ\* (one-minute labels 2019), CLIF 2025 blank-location files.

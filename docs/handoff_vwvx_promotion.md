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

**Implemented (Codex 2026-11-24 F4 and 2026-11-26 F1–F5), awaiting Codex re-review:**
- Comparator: eqserver `scan/compare_lt.py` at 6bc289a. **Pinned sha256:
  `c18f0a3bbc5b3f9daa2e4db811f146a7059ce911f40b6db20004848a1b483ddf`.** Staged files are validated before any verdict, including `write`.
- `sds_staging_ledger` branch `verdict-mode` (latest commit on that branch):
  `apply.py --verdicts F --comparator-sha <pin>`. Refuses a verdict file with
  duplicate paths or any other comparator. `identical` requires LT present and
  unchanged. Every file action is idempotent: a file this run already
  installed is recognised from its bytes and backup, and only its missing
  ledger event is written. Any held or failed file makes a commit exit
  non-zero. Tests: `tests/test_apply_verdicts.py` (17).
- eqserver branch `promote-verdicts` (d4a0520): `run_production_promote.py
  --verdicts-dir D --comparator-sha <pin>`. Two-way inventory with recorded
  sizes; the comparison file must cover exactly the manifest's files, once
  each, from the pinned comparator; a unit is recorded promoted only when its
  commit exits 0 with held = 0 and fail = 0. Tests:
  `scan/test_promote_verdicts.py` (10).

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

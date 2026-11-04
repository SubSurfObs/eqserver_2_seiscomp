# Handoff — promote DU (release v2) from staging to the long-term archive

For the agent that runs promotion. Written 2026-11-04 by the conversion
session. Read `AGENTS.md` first (hard invariants 1–8 apply).

## What you are moving

DU waveforms converted by release v2 (and its targeted rebuilds) into the
shared staging SDS `/mnt/seiscomp_staging/seiscomp_archive/<YEAR>/DU/<STA>/`.
About **1.5 TB**, ~264 station-years. Every unit is recorded in a queue as
`convert_done`; promotion copies its day-files into LT through
`sds_staging_ledger/apply.py` and records the provenance in the ledger.

## Where it runs

**dev1 only** (`seiscomp@seismology-dev1.its.unimelb.edu.au`), the only host
with `/mnt/seiscomp_archive` mounted **rw**. The staging VM and the Mac cannot
promote (LT is read-only there; the Mac currently cannot even reach dev1).
Run the dev1 preflight before anything else:

```
hostname
mount | grep seiscomp_archive        # must show rw
touch /mnt/seiscomp_archive/.preflight_$$ && rm /mnt/seiscomp_archive/.preflight_$$
```

## One-time setup (before the first promote)

1. On dev1, `git pull` the eqserver repo (branch `workflow-v3`) and the ledger
   repo; both must be clean.
2. **Pin the ledger** (Codex c835400 F2 — the strict Stage-5 preflight aborts
   on `sds_staging_ledger: no pin set`): record the reviewed ledger HEAD in
   `config/pinned_repo_shas.yaml` (`sds_staging_ledger.pinned`), commit, push,
   pull on dev1. This is an operator decision — confirm the SHA with Dan.
3. Run records are already repaired (2026-11-04, `scan/backfill_run_manifests.py`,
   281 manifests: one run ID, release SHAs, archived file-list path + sha256;
   originals kept as `*.pre_backfill`). Plans are mirrored on the share at
   `<queue>/plans/DU/`, so `policy_yaml_path` resolves from dev1.

## Which queues to promote (and only these)

| Queue (`/mnt/seiscomp_staging/…`) | What it holds |
|---|---|
| `eqserver_sweep_du_v2_s0` … `_s5` | the main release-v2 run |
| `eqserver_sweep_du_v2_rebuild_s0` | MRAT 2017/2019 relay-file rebuild |
| `eqserver_sweep_du_v2_rebuild_s1` | v2.1: LILH 2016–17, ARKL 2018, PLYP 2025, S88U 2018 |
| `eqserver_sweep_du_v2_rebuild_s2` | v2.2: PiesMo 2025 (10 stations) |
| `eqserver_sweep_du_v2_rebuild_s3` | v2.3: WEPH 2025, RNDA 2017, LILH 2018 |

**Never promote** `eqserver_sweep` (VW v1), `eqserver_sweep_w2_*`,
`eqserver_sweep_du_v1_*`, `eqserver_sweep_v3*` or any test queue — those runs
are superseded. Where a rebuild replaced a unit, the original is **held** in
its v2 queue, so the order you process queues in does not matter.

Holds are honoured automatically: `run_production_promote.py` skips any run
with an active hold; a later `{"action": "released", "releases": <prefix>}`
event lifts only holds whose reason starts with that prefix.

## How to run

Start small, one queue, `--once`, and inspect before going wider:

```
python3 scan/run_production_promote.py \
    --staging-root /mnt/seiscomp_staging/seiscomp_archive \
    --lt-root /mnt/seiscomp_archive \
    --ledger-root ~/projects/SubSurfObs/sds_staging_ledger/seiscomp_archive \
    --queue-dir /mnt/seiscomp_staging/eqserver_sweep_du_v2_s4 \
    --once
```

The script dry-runs `apply.py --mode decide` per unit and commits **only if
there are no overrides**; any override goes to `held.jsonl` for review. Never
use `--fast`.

Suggested first unit: a small single-year station (e.g. KBRI 2025 or JMS3
2024). Check, before scaling up:
- the LT day-files exist with the expected channel names and sizes;
- `<LT>/<YEAR>/DU/<STA>.events.jsonl` lines carry `source.kind=eqserver`, a
  `run_id`, `policy_sha`, and the ledger has `policies/<sha>.yaml` and
  `runs/<run_id>/run.json`;
- throughput (files/s and GB/h to mediaflux), to plan the full ~1.5 TB.

Expect **overrides** for recent DU years: many DU stations already have
2025–26 data in LT from the live SeisComP feed. Those units will be held —
correct behaviour; they need per-unit review (do not bulk-commit holds).

## Not ready — leave alone

- LKHRT 2022–24 (failed; collision rule under Codex review), LILH 2019
  (1 collision day, Codex review).
- Stations not yet converted: TPSOP, ADE2, GLN, MPTV, MYP, GLDS, THS, BLAT,
  MVM1Y, and the include-unknown DNL3/DNL4/NNP3/PLY3/RNDA2.
- MVM1Y and BLAT have no output to promote.

## Afterwards

Staging copies may stay as a redundant backup (mediaflux is versioned and
soft-delete-recoverable); clear them later with `cleanup.py` once LT == staging
is verified. Record outcomes in `STATUS.md` and the ledger.

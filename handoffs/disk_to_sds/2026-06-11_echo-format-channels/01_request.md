# Handoff to disk_to_sds — Echo-format channel-name recognition in suds_convert.py

**From:** eqserver_2_seiscomp (Dan)
**Date:** 2026-06-11
**Priority:** Medium — silent data loss for one VX cohort, not blocking VW work, but blocking VX cleanup.

## Problem (one paragraph)

`disk_to_sds/scripts/suds_convert.py` recognises only **Kelunji EchoPro** channel naming (`c01/c02/c03` → velocity Z/N/E; `c04+` dropped). It does **not** recognise the older **Kelunji Echo** channel naming (`Up-T/East-T/North-T` for velocity; `Up-A/East-A/North-A` for accelerometer). On Echo-format SUDS files, every channel — including velocity — falls into the catch-all `dropped` set and **zero traces are written**. This shows up in the convert log as `dropped=['East-A', 'East-T', 'North-A', 'North-T', 'Up-A', 'Up-T']` and `traces=0` and `WRITE=0`.

Discovered today during VX sweep cleanup audit: VX MOE3/MOE4/MOE5/MOE6/MOE8 2012 are all Echo-format stations and were silently dropped end-to-end. The convert step rc=0, the promote step rc=0 (because staging is empty so apply.py has nothing to write), and the unit ends up recorded as "promoted" with `write=0` in promote_done.jsonl. Pure silent loss.

CLAUDE.md (eqserver_2_seiscomp) flags the Echo vs EchoPro split as a "WORKING HYPOTHESIS" — one Echo sample from SDAN 2017+2018 plus operator confirmation, with disk_to_sds working only on the EchoPro side. This handoff turns the hypothesis into a converter capability.

## Exact code location

`disk_to_sds/scripts/suds_convert.py`:

```python
# Line 27 — current EchoPro mapping
ECHOPRO_ORIENT = {"c01": "N", "c02": "E", "c03": "Z"}  # c04 = microphone -> excluded

# Lines 102-105 — dispatch
for tr in raw:
    orient = ECHOPRO_ORIENT.get(tr.stats.channel)
    if orient is None:
        dropped.add(tr.stats.channel)
        continue
```

The raw `tr.stats.channel` for Echo-format files (per sudspy.scan_suds_file probe today) is:

| Raw channel | Semantic | Target SEED orientation |
|---|---|---|
| `Up-T`     | Vertical velocity (Translation) | `Z` |
| `North-T`  | North velocity                  | `N` |
| `East-T`   | East velocity                   | `E` |
| `Up-A`     | Vertical accelerometer          | drop (per existing convention) |
| `North-A`  | North accelerometer             | drop |
| `East-A`   | East accelerometer              | drop |

## Proposed patch (minimal)

```python
# After the existing ECHOPRO_ORIENT dict at line 27, add:
ECHO_ORIENT = {"Up-T": "Z", "North-T": "N", "East-T": "E"}
# Up-A / North-A / East-A are accelerometer — fall through to dropped[]
# (consistent with c04+ being dropped on EchoPro).

# Replace the dispatch lines 102-105 with a fallthrough lookup:
for tr in raw:
    orient = ECHOPRO_ORIENT.get(tr.stats.channel) \
          or ECHO_ORIENT.get(tr.stats.channel)
    if orient is None:
        dropped.add(tr.stats.channel)
        continue
```

That's it. The downstream `channel_for(orient, rate, ...)` works the same regardless of which dict the orientation came from — it cares about (orientation, sample_rate), not the source naming convention.

## Sample data to verify against (on the staging VM)

```
ssh dsand@172.26.144.41
ls /mnt/eqserver_archive/shared/data/repository/archive/MOE3/continuous/2012/06/24/
# pick one .dmx, e.g. 2012-06-24\ 0000\ 32\ MOE3.dmx
```

A representative file already inspected:
- `2012-06-24 0000 32 MOE3.dmx` — 100 sps, 6 channels (`Up-T/North-T/East-T/Up-A/North-A/East-A`), per `sudspy.scan_suds_file`.

The unit-test pattern: run `convert_suds_files()` on one day's files for MOE3 2012-06-24. Expected post-patch: `qc["dropped_components"] = ["East-A", "North-A", "Up-A"]` (just the accelerometers); `len(out) == 3 * n_files_with_data` (one velocity trace per channel per file, summed).

## Cross-repo coordination after the patch lands

eqserver_2_seiscomp will:

1. Pin the new engine SHA in `CLAUDE.md` "Shared conversion core" section.
2. Re-convert the affected VX units against the new engine, gap-filling via `apply.py --mode decide --commit`. Likely scope:
   - VX.MOE3 2012, VX.MOE4 2012, VX.MOE5 2012, VX.MOE6 2012, VX.MOE8 2012 — confirmed Echo-format.
   - VX.CREM 2012 — different shape (c04-c06 only in sampled files); verify separately with a sample probe before deciding.
3. Record results in `docs/scan1_recovery_register.md` (eqserver_2_seiscomp) as a new Issue.

VW promoted data is unaffected — full scan of VW convert logs confirms zero `Up-T/East-T/North-T` drops. The patch does not affect VW; it only opens up VX recovery + protects future SD cards from any Echo-format stations (if any appear).

## Why this is silent (and why it stayed silent through VW)

- VW sweep produced 334 unit-years and **none** contained Echo-format data. The classifier was effectively never tested against this naming convention.
- The convert log writes `dropped=[…]` per day, but no downstream step reads that field to flag "all source channels dropped" as an error. Combined with rc=0 from convert and rc=0 from apply (empty staging → write=0), the only signal of loss is the promote_done summary having `write=0` after a non-empty source archive.
- A future hardening (separate from this patch) might be: if `len(out) == 0` and `len(read_errors) == 0` and `len(dropped) > 0`, raise rather than silently emit an empty stream. That would have caught the MOE* days at convert time.

## Engine provenance, per shared CLAUDE.md rule

After the patch, please:
- Commit to `main`, push, and let me pull the new SHA on the staging VM via `git pull` (the standard git-synced-across-hosts rule from this morning's incident still applies — no side-loading).
- Reply on this thread with the new SHA so I can pin it in eqserver_2_seiscomp's CLAUDE.md and re-run.

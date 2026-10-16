---
name: project-stress-test-results
description: "2026-06-01 multi-station + setsid-detach + resume-after-kill stress test. All three operational gaps from yesterday's failed sweep launch are now verified resolved. The pipeline is operationally ready for the full VW sweep."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**On 2026-06-01 afternoon a stress test ran against the same isolated mediaflux
proxy LT + local-bare ledger as the morning correctness test. Goal was to verify
the OPERATIONAL launch path that the morning test couldn't reach.**

## Operational gaps from yesterday that this test verified

1. **Long-lived process detachment.** Yesterday's `nohup CMD > LOG 2>&1 &`
   pattern died when the launching SSH disconnected. Today's `setsid -f bash -c
   'exec CMD > LOG 2>&1' < /dev/null` survives SSH exit cleanly — confirmed by
   verifying PID from a fresh SSH session after the launcher disconnected.
2. **Multi-station consecutive runs.** Morning test only used HOLS. Today's
   stress test queued 11 units across 5 different stations (DEAL, HOGN, LRWS,
   LRSE, MOSU) crossing year and station boundaries.
3. **Resume after mid-flight kill.** Yesterday's `pkill` was reactive cleanup
   after the fact. Today's test deliberately killed `convert.py + phase3` as a
   process group (`kill -KILL -PGID`) mid-conversion and verified clean resume.

## What the test verified

| Property | Status |
|---|---|
| `setsid -f` detach survives SSH disconnect | PASS — all three watchers (convert/promote/cleanup) alive after launcher SSH ended |
| Process-group kill catches phase3 child cleanly | PASS — `kill -KILL -PGID` killed both convert.py and phase3 with no orphan |
| Killed unit NOT in `convert_done.jsonl` | PASS — append happens only on phase3 rc=0 |
| Restart correctly identifies killed unit as still-to-do | PASS — "13 already done in queue / 1 work unit remaining" output |
| Restart re-queues the killed unit | PASS — HOLS 2018 re-launched with a fresh run_manifest timestamp |
| Cross-station events.jsonl isolation | PASS — each `<YEAR>/VW/<STA>.events.jsonl` had only its own days + 1 unique run_id (per S1+C1 assertions) |
| Cross-year events.jsonl isolation | PASS — every day-year matches the file's year prefix |
| LT files isolated to expected subtrees | PASS — no `/2019/VW/DEAL/HHE.D/` file showed up under DEAL 2020 or HOGN |
| Bare ledger advanced per unit | 7 new commits = 7 successfully promoted units (3 skip-empty entries correctly got no commit) |
| `skip-empty` short-circuit | PASS — items_succeeded=0 entries got `action: skip-empty` in promote_done, no apply.py invocation, no commit |
| Real ledger HEAD unchanged | `1e01e77` (same as test start) |
| Real github main unchanged | `1e01e77` |
| Production paths untouched | prod sweep dir mtime unchanged |

## The detach recipe (record for any future launch)

```bash
setsid -f bash -c 'exec PYTHON SCRIPT [args] > LOG 2>&1' < /dev/null
```

Why each part matters:
- `setsid` — new session, no controlling terminal, immune to terminal SIGHUP
- `-f` — fork into background after setsid (so the launcher SSH can exit)
- `bash -c 'exec ...'` — `exec` replaces bash with the python process directly,
  so there's no bash wrapper hanging around (cleaner `pgrep`, cleaner `kill`)
- `> LOG 2>&1` — redirect stdout+stderr to a file
- `< /dev/null` — disconnect stdin so the process never blocks on a read

To kill a `setsid`-launched process cleanly: `kill -KILL -<PGID>` (negative PID
= kill the entire process group, which catches phase3 child too).

## What this test did NOT prove

- **Long-running stability beyond ~20 min.** All resume-test phase3s here ran
  10–40 min individually; the full VW sweep is hundreds of units over multiple
  days. We have no direct evidence the watchers don't memory-leak / blockage
  over that horizon.
- **Realistic mid-run LT overlap density.** Test LT was empty at start; the
  real LT has substantial direct-seedlink overlap for recent years. We've
  verified the SKIP and OVERRIDE paths work individually but haven't measured
  the held.jsonl rate at scale.
- **Concurrent promote/cleanup behavior under load.** The watchers poll 30s
  / 60s respectively; the test never saturated their input queue.

These are sensible follow-ups but not blockers for launching the sweep with
monitoring.

## Plans regenerated and committed

Same session: all 41 VW plans regenerated against the simplified plan_generator
and committed as `8d89b07` on `rewrite-suds2sds`. 15 files changed (7
previously-BLOCKED → ok, 8 previously-needs_review → ok); the other 26 stations
were already ok and produced identical YAMLs. All 41 plans now `status: ok`.

## Forensics preserved

Test artifacts at `/var/tmp/test_round_2026-06-01_stress/` on dev1 and
`/mnt/seiscomp_staging/test_round_2026-06-01_stress/` on staging VM. Test LT
under `/mnt/test_lt/eqserver_stress_20260601/` (mediaflux scratch share, also
mounted on staging VM ro). Local bare ledger at
`/var/tmp/test_round_2026-06-01_stress/test_ledger_bare.git`.

To tear down after sign-off:
- `sudo umount /mnt/test_lt` on dev1 and on staging VM
- `rm -rf /var/tmp/test_round_2026-06-01_stress` on dev1 and staging VM
- `rm -rf /mnt/seiscomp_staging/test_round_2026-06-01_stress`
- `rm -rf /mnt/test_lt/eqserver_stress_20260601` (the named scratch dir on the
  shared mediaflux volume; needs the mount back to do this)

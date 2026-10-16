---
name: project-engine-provenance-incident-2026-06-01
description: "Mid-sweep on 2026-06-01, disk_to_sds caught that the SUDS→SDS engine running on the production sweep existed only as an untracked side-loaded file on the staging VM — no git record. Functionally identical to the Mac working tree, but the running 2.6 TB conversion had no pinnable engine SHA. Pinned at disk_to_sds 9a3b2ae after the fact."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

**On 2026-06-01 mid-VW-sweep, a second instance of the side-loading
problem (already prohibited by [[feedback-git-synced-across-hosts]])
was caught and triaged by the disk_to_sds session.**

## What happened

The production VW sweep launched on 2026-06-01 ~04:36 UTC was importing
`disk_to_sds/scripts/suds_convert.py` (the SUDS→SDS engine) from
`/home/unimelb.edu.au/dsand/projects/SubSurfObs/disk_to_sds/scripts/`
on the staging VM. That file was **untracked in git** (`??` in
`git status`), and the VM's `disk_to_sds` checkout was 14 commits stale
(at `014428d`, the repo-rename commit, from before suds_convert.py was
ever committed). The Mac had 17 uncommitted lines of work on the same
file, which had also never been committed.

So the engine actually doing the converting existed in git nowhere.

## How it was caught

The disk_to_sds Claude session spotted the divergence while doing a
pre-macOS-update audit. They byte-compared the three possible versions
(Mac working tree, Mac HEAD, VM untracked file) and found
**VM-untracked == Mac-working-tree, byte-identical, blob hash 7f625589**.
sudspy was matching too. So:
- Functionally fine: engine + sudspy versions on the VM are consistent
  and match what the Mac had in flight.
- Provenance broken: no commit on either host pinned the bytes that
  were actually converting the archive.

## How it was fixed

Committed the Mac's working-tree version to `disk_to_sds/main` as
**`9a3b2ae`** (full sha: `9a3b2aead0232bb21a449170d265c8dfc117cf68`).
Verified the committed blob hashes to `7f625589` — byte-identical to
the VM's running copy.

## State at fix

- The live convert.py / phase3 processes keep using their in-memory
  module copy regardless of disk state. No restart needed; the sweep
  proceeds unmodified.
- All `(sta, year)` units phase3 imports FROM THIS POINT FORWARD will
  load whatever's on disk. The current sweep's units between 04:36 UTC
  and the eventual VM sync will have loaded the untracked-but-identical
  pre-fix copy; functionally fine, provenance backfilled by SHA equality.
- Still pending at memory-write time: VM-side disk_to_sds checkout
  needs `git stash -u` (or `rm` the untracked file since it equals
  what's incoming) → `git pull` to bring it current at `9a3b2ae`.

## Why both Claude sessions failed to catch this earlier

- The disk_to_sds engine work was developed and side-loaded "to make
  the sweep work" under time pressure (post-2026-05-30, pre-launch).
  Never committed because the workflow rhythm was: "test on VM, iterate,
  test again." Commit never reached the top of the queue.
- eqserver's CLAUDE.md referenced `disk_to_sds/scripts/suds_convert.py`
  without pinning a SHA. The reference implicitly trusted that the file
  existed at HEAD on both hosts. It didn't.
- The eqserver sweep's `events.jsonl` source dict records `project_git`
  (the eqserver SHA) and `policy_sha` (the per-station plan), but NOT
  an engine SHA. So there was no scientific-provenance pressure
  surfacing the engine's git state.

## Tightening — concrete follow-ups

1. **Engine SHA pinned in CLAUDE.md** at `9a3b2ae` (done 2026-06-01,
   see "Shared conversion core" section).
2. **Add `disk_to_sds_engine_sha` to events.jsonl source dict** in
   apply.py. Symmetric to the existing `project_git` field. Deferred
   to post-sweep so the current run's heterogeneous-fields awkwardness
   stays contained. Will apply for BEST 2019 retry and DU sweep.
3. **Pre-launch audit checklist for any future sweep:** verify that
   every import-target on the VM (a) is in git, (b) is at a SHA known
   to match the operator's intended version, (c) is referenced from
   CLAUDE.md or equivalent with that SHA explicit.

## Cross-references

- [[feedback-git-synced-across-hosts]] — the rule that was violated, twice now.
- [[feedback-test-the-pipeline-not-just-the-unit]] — the pre-flight
  audit pattern that should have surfaced this. Need to add a "git-tracked
  on import-host" check to that pre-flight set.
- `handoffs/disk_to_sds/2026-06-01_engine-provenance/01_engine_committed_*.md`
  — disk_to_sds's incident write-up.
- CLAUDE.md "Shared conversion core" section — the pin.

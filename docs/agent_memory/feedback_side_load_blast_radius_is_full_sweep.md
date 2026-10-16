---
name: feedback-side-load-blast-radius-is-full-sweep
description: "When a side-loaded engine corrupts archive bytes, the blast radius can approach the cost of a full re-sweep. Provenance discipline must be enforced BEFORE phase3 starts, not after."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

A side-loaded (non-git-tracked) engine running on the VM during a production sweep can corrupt every station-year converted in that window. The cleanup cost can approach the cost of doing the full sweep over.

**Real example 2026-06-18:** the engine side-load window was just 11 days (2026-06-01 to 2026-06-12). Convert ran for 320 VW station-years in that window. Even with optimistic recorder-type filtering, the re-conversion scope is 100-150 station-years — operator commented "by that time you'd almost be wondering whether you just try to do a clean run and do everything."

**Why this is structural, not incidental:**
- The engine code IS correct on current commits; the bug is in what code ACTUALLY ran on the VM during the sweep
- There's no per-byte audit trail of "which engine SHA wrote this LT file"
- Detection of bad bytes is only possible by downstream consumers complaining (which happened) or expensive per-day source-vs-LT diffs
- The only effective remedy is re-conversion with verified-current engine

**How to apply:**

Before launching any production sweep (DU, future VW resumes, future networks):

1. Verify `disk_to_sds` checkout on the VM matches a committed SHA:
   ```
   ssh dsand@<vm> 'cd ~/projects/SubSurfObs/disk_to_sds && \
       git fetch && \
       git status -uno && \
       sha256sum scripts/suds_convert.py'
   ```
2. Compare the SHA against `git log -1 --format=%H scripts/suds_convert.py` on the canonical remote
3. If they don't match (or working tree is dirty), STOP — do not start the sweep until reconciled
4. Record the pinned engine SHA in CLAUDE.md "Engine pin" section BEFORE the sweep, not after

This is non-negotiable for any production-byte-writing run. Operator stance 2026-06-18: "provenance discipline isn't a nicety."

Linked: [[project-engine-provenance-incident-2026-06-01]], [[feedback-git-synced-across-hosts]].

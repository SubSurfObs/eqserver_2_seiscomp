---
name: project-split-suds-convert-followup
description: "Queued cross-repo rename: disk_to_sds will split suds_convert.py into sds_writer.py + suds_convert.py after current sweep stabilises. Eqserver's phase3_driver.py imports must update in the same window."
metadata: 
  node_type: memory
  type: project
  originSessionId: 66713a2d-7a0d-44f1-ae1a-e3a1e4de0f66
---

After the current VW sweep stabilises, `disk_to_sds` will split
`scripts/suds_convert.py` into two files so the names match the contents:

- `sds_writer.py` — generic SDS writer: `write_sds`, `_sds_day_path`, atomic
  `.partial`+rename plumbing, and the post-`00b6835` read-merge-write logic.
  Nothing SUDS-format-aware.
- `suds_convert.py` — keeps only the SUDS reader path: `convert_suds_files`,
  `network_for_station`, anything SUDS-format-aware. The name is then
  accurate to what's inside.

**Why:** The file's name is accurate for the SUDS reader but misleading for
the generic SDS writer that every gecko/minimus branch in eqserver phase3
actually calls. Operator hit confusion when fixing the midnight-boundary
bug in `disk_to_sds`'s `write_sds` — "we're fixing the gecko bug, and the
fix goes in `suds_convert.py`?" Naming friction was high enough to flag.
Tracked on disk_to_sds side too as of `disk_to_sds@2ee96f3` CLAUDE.md note.

**How to apply:**

1. **Wait for the current sweep to stabilise.** The rename is cosmetic —
   not load-bearing for correctness — and the import-path coordination
   across two repos doesn't want to happen under sweep pressure.
2. **Coordinated PRs.** When disk_to_sds ships the split, eqserver's
   `scan/phase3_driver.py` must update in the same window:
   - Line ~138 in `_write_sds_retry`:
     `return suds_convert.write_sds(stream, staging_root)` → callsite stays
     the same shape, but the worker imports `sds_writer` instead of (or in
     addition to) `suds_convert`.
   - The pickle-safe worker `_worker_convert_day` (around line 540) does
     `sys.path.insert(0, job["disk_to_sds"]); import suds_convert` —
     update to also `import sds_writer` and pass it through, OR have
     `_write_sds_retry` take `sds_writer` as its first arg.
   - Any `suds_convert._sds_day_path` references in the dry-run path also
     need updating (used to compute `sds_files_planned`).
3. **Keep the diff pure rename.** No behavior changes, no API changes —
   makes the diff easy to review and easy to revert if any phase3 unit
   pattern breaks.
4. **Re-pin in CLAUDE.md.** Bump the "Engine pin (current)" line in
   eqserver CLAUDE.md to the new disk_to_sds SHA that contains the split.
5. **Verify on VM.** After both PRs merge: `git pull` on the VM's
   disk_to_sds AND eqserver checkouts; spawn a test phase3 unit; confirm
   import succeeds and a real day-file gets written through the new
   `sds_writer.write_sds`.

Related: [[project-engine-provenance-incident-2026-06-01]] for why this
discipline exists in the first place;
[[feedback-git-synced-across-hosts]] for the no-scp rule that makes
import-path migrations work cleanly across the Mac and the VM.

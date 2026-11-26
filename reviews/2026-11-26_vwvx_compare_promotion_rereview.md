# Review: VW/VX comparison fixes, verdict promotion, and first converted units  (2026-11-26, reviewer: Codex)

## Verdict
FIX-THEN-PROCEED.

The VW/VX conversion should continue. Do not start VW/VX promotion yet. The comparator fixes work when LT exists, and the converted-data spot checks were clean, but the absent-LT path and the copy transaction still have fail-closed gaps. These need focused code and regression fixes only; no reconversion or new campaign is requested.

## Findings

### F1 — blocker — An absent LT file bypasses all staged-file validation
- Where: `scan/compare_lt.py:112-129`; missing regression in `scan/test_compare_lt.py`
- Status: verified
- Scenario: `compare_pair` snapshots staging and immediately returns `write` at lines 116–118 when LT is absent. `_canon` is never called for staging. Consequently an unreadable staged file—or one with the wrong identity, out-of-day samples, ambiguous duplicate values, or overlapping rates—gets an approved `write` verdict. An adversarial file containing only `not miniSEED`, but carrying a valid SDS filename, returned `write`. This is directly relevant to LOCU 2014–16 and the other years absent from LT.
- Smallest fix or check: canonicalise and validate staging before testing whether LT exists. If staging is invalid, return `conflict` or `unreadable`; only a validated staged file can receive `write`. Add absent-LT regressions for unreadable bytes, wrong identity, out-of-day samples, differing duplicates, and overlapping rates.

### F2 — blocker — `identical` does not check that LT still exists or still has the compared hash
- Where: `../sds_staging_ledger/apply.py:145-173` at `db14dd8`; missing regressions in `tests/test_apply_verdicts.py`
- Status: verified
- Scenario: staging is re-hashed for every verdict, but the `identical` branch at lines 160–161 returns `skip` without checking LT. Direct adversarial calls returned `skip` both after LT was changed and after LT was deleted. The documented claim that changed LT bytes hold fail-closed is therefore false for the most common verdict.
- Smallest fix or check: for `identical`, require LT to exist and its current hash to equal `lt_sha256`; otherwise hold. Add changed-LT and missing-LT tests for `identical`.

### F3 — blocker — The dry-run/commit transaction can record a partially held unit as promoted and is not fully restart-safe
- Where: `scan/run_production_promote.py:282-343` at `adb9064`; `../sds_staging_ledger/apply.py:347-437` at `db14dd8`
- Status: verified
- Scenario: the promoter checks `held` and `fail` only in the dry run. During commit, `apply.py` can newly hold a file because staging or LT changed, or fail after earlier files were copied; it still exits zero. The promoter then unconditionally appends `promoted`, without checking the commit summary's `held`/`fail` counts. There are also uncovered crash windows: after a new file is installed but before its ledger event, or after ledger append but before `promote_done`, rerunning the same `write`/`superset` verdict sees the installed LT bytes as an unexpected change and holds rather than completing idempotently. The existing test covers only interruption after the old LT rename and before the new copy.
- Smallest fix or check: make a commit with any hold/failure return non-zero, and have the promoter independently require a parsed commit summary with `held == 0` and `fail == 0` before writing `promote_done`. Make each file action idempotent by recognising the verified installed-staged/verified-backup/ledger-event states for the same run. Add crash-point tests after backup rename, after install, after ledger append, and before `promote_done`. For live files, revalidate the renamed backup hash before installing staging; use no-clobber creation for an absent-LT `write` so a file appearing after the check cannot be overwritten.

### F4 — blocker — The “exact inventory” guard detects extra staged files but not missing ones
- Where: `scan/run_production_promote.py:82-101` at `adb9064`; existing `scan/test_inventory_guard.py`
- Status: verified
- Scenario: `unnamed_staged_files` computes `staged - manifest`. If a file named in the completed run manifest has disappeared from staging, the function returns an empty list and promotion scans only the remaining files. If the whole station-year directory is missing, line 98 explicitly returns an empty list. A unit can therefore be recorded promoted without all files produced by its conversion. The existing “exact inventory” test covers only an extra staged file.
- Smallest fix or check: compare both directions and verify the recorded byte sizes: every active staged file must be named, and every manifest-named file must exist at its expected size. Require the verdict path set to equal that exact inventory too. Add one-missing-file and missing-unit-directory regressions.

### F5 — blocker — The copy step records but does not enforce the approved comparator identity
- Where: `../sds_staging_ledger/apply.py:136-142`, `../sds_staging_ledger/apply.py:421-427` at `db14dd8`; `scan/run_production_promote.py:274-280` at `adb9064`
- Status: verified
- Scenario: each result records `comparator_sha256`, but `load_verdicts`, `verdict_action`, and the promoter accept any value. A result made by the superseded comparator from before the duplicate/identity fixes has the same JSON fields and can authorize a copy. The SHA is currently provenance after the fact, not a pin.
- Smallest fix or check: the promoter should pin the approved `scan/compare_lt.py` SHA and require every verdict record to carry exactly it; reject missing, mixed, or unexpected comparator SHAs and duplicate verdict paths. Add one wrong-comparator regression.

## Checked and found OK

- The rewritten comparator closes the original duplicate, identity, rate, stable-byte and rational-time defects when LT exists. All 21 supplied comparator tests passed on the staging VM from the exact reviewed files.
- The ten supplied `apply.py --verdicts` tests passed on the staging VM. Superset backup naming, installed-file verification, conflict/no-verdict holds, and the tested interrupted-rename recovery work as claimed.
- DDSW 2021 is safe to accept under the existing damaged-source ruling, scoped to this observed file class rather than as a blanket rule for every future record-scan fallback. Its 121 flagged days name 259 source files. Twelve files distributed from March to November were decoded record-by-record: all 536,708 valid record samples had exactly the same stated times and values as the whole-file fallback. Three of those intervals were then compared with staging: all 133,292 decoded source samples were present exactly, with none missing or different. The files contain small 256/512-byte unframed fragments that cannot be treated as valid MiniSEED; those fragments should remain recorded as source damage.
- Independent source-to-staging minute checks passed:
  - VX ALLA 2012: 34,500 independently decoded SUDS samples exact;
  - VW LOCU 2014, 2015 and 2016: 45,000 + 36,000 + 30,000 samples exact (including the 2015 HH* naming epoch);
  - VW HOLS 2015: 45,000 samples exact.
  Across these five checks there were zero missing, altered, duplicated, phantom, or unselected-only samples, and all output identities were correct.
- LOCU 2014–16 genuinely has no LT day-files. This makes F1 important, but the sampled staged data itself was correct.
- For a VW unit already in LT, HOLS 2015 day 183 was `identical` in staging and LT for CHE, CHN and CHZ: 21,600,000 samples per channel, exact time and value.
- The VX queue has 32 terminal station-year attempts, but its precise state is 29 `convert_done` plus three `convert_failed` (DR03 2015, DR03 2016, RICA 2012). Those three must remain held/accepted-by-ruling/rebuilt as appropriate; they are not completed promotable units.
- No evidence from these spot checks requires stopping or repeating the release-v3.0 conversion.

## Response


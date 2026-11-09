# Review: DU overlaps, excluded `.N.dmx` files, and release v2.4  (2026-11-09, reviewer: Codex)

## Verdict

FIX-THEN-PROCEED.

Release v2.4's waveform output is fit to keep, but its six run manifests must
be repaired before promotion because they do not identify the `sudspy` code.
The four held collision cohorts need the single precedence rule specified in
F1 before conversion. Neither issue requires another broad campaign or a
waveform rerun of the five v2.4 stations.

## Findings

### F1 — blocker — use one timestamp-ordered overlay rule for the held miniSEED collisions

- Where: `scan/phase3_driver.py:1189-1205`,
  `scan/phase3_driver.py:1218-1270`, `scan/lib/resolver.py:23-32`;
  `~/v3_test/g1/BLAT.2023`, `~/v3_test/g1/TPSOP.2017`,
  `~/v3_test/g1/TPSOP.2018`, `~/v3_test/lkhrt`; LILH source files
  `2019-04-06 1924 00 LILH_HHZ.mseed.zip` and
  `2019-04-06 1936 00 LILH_HHZ.mseed.zip`.
- Status: verified.
- Scenario: these are multiple miniSEED packets in one source file with the
  same final station/location/channel and rate. Their stated time ranges
  overlap, but the values in the overlap differ. They are not extra locations
  being accidentally combined by the auditor. Examples checked directly:
  BLAT has both deep overlaps and packets offset by 0.2 sample; TPSOP has
  one-sample seams and overlaps of hundreds of samples; LKHRT has one-sample,
  deep, on-grid and 0.2-sample off-grid cases. LILH's two problem files have
  respectively 2,800 and 1,200 conflicting 400 Hz samples; correlations are
  about 0.05 and -0.11, so they are not duplicate copies. The headers provide
  no second sensor identity and cannot tell which conflicting value is
  physically preferable. The present loud check discards each whole file,
  causing much greater loss than the ambiguous interval warrants.
- Smallest fix or check: use this one rule for TPSOP, BLAT, LKHRT and LILH:
  **for traces in one file that map to the same final identity and rate, sort
  by stated start time; the earliest-starting packet owns its half-open time
  span, and later packets contribute only samples outside already occupied
  spans. Use source record order only to break an equal-start tie.** This is
  the same basic overlay already implemented in `resolver.py`, and it matches
  the effective earliest-start behaviour of the pre-collision-check resolver.
  Do not average, interpolate, shift timestamps or join grids.

  For an off-grid conflict, apply the same rule in continuous integer-
  nanosecond time: remove the later packet's occupied portion and retain its
  first genuinely uncovered source sample on its original grid. A sub-sample
  gap is honest; snapping the packet to the first grid is not. Record the file,
  final identity, fractional grid offset and displaced sample count. Different
  rates or collisions not covered by this exact same-identity rule must still
  fail loudly. The acceptance test is exact source-valued output, no output
  overlap, no whole-file discard, and manifest evidence for every displaced
  interval. The auditor must understand this logged precedence and verify the
  chosen packet rather than require both mutually inconsistent values.

  The auditor's `missing_s` is a union by terminal orientation
  (`scan/qa_verify.py:835-871`), not a sum over source header locations or
  identities. It checks wrong locations separately as `seed_id_mismatch`.
  TPSOP's approximately 258,284 source-seconds on a near-full three-component
  day is consistent with E/N/Z coverage once each, not duplicated identities.

### F2 — blocker — all six v2.4 manifests omit the `sudspy` release identity

- Where:
  `/mnt/seiscomp_staging/eqserver_sweep_du_v2_rebuild_s4/run_manifests/*.json`;
  `scan/phase3_driver.py:1813-1826`.
- Status: verified.
- Scenario: every ADE2, GLN, MPTV, MVM1Y and MYP run manifest says
  `"sudspy": "?"`. The matching `convert_done.jsonl` records do prove that
  strict preflight used `sudspy` commit `753feaf`, so this is a manifest
  bookkeeping defect rather than evidence that unpinned code ran. However, a
  promoted run record with an unknown reader identity violates the required
  provenance. The six selection databases also remain only in the VM working
  directory; no corresponding v2.4 archives were found under
  `eqserver_v3_selections`.
- Smallest fix or check: archive the six selection databases, run the existing
  manifest backfill from their queue preflight records, and verify the
  resulting selection hashes and three full release SHAs before adding s4 to
  promotion. Do not reconvert the waveforms. Add a regression that rejects
  `?` in any committed run manifest's release block.

### F3 — should-fix — MVM1Y's written plan does not enumerate its actual output identities

- Where: `plans/DU/DU.MVM1Y.plan.yaml:13-29`,
  `scan/phase3_driver.py:1182-1188`.
- Status: verified.
- Scenario: the reviewed plan lists only `DHE/DHN/DHZ`, while
  `sp_band_by_rate` correctly produces `EHE/EHN/EHZ` at 100 Hz and `DH*` at
  250 Hz. An independent checker that treated the plan's channel list as the
  contract therefore reported the 100 Hz portion as missing/phantom. Direct
  checks show the conversion itself is right: on 2021-10-09, 18,000 samples
  at 100 Hz matched exactly under `EH*`, and 45,000 samples at 250 Hz matched
  exactly under `DH*`.
- Smallest fix or check: make the plan express both rate-conditioned output
  families, or make the channel-contract schema explicitly point to
  `sp_band_by_rate` and require every plan consumer to apply it. This is not a
  reason to rerun MVM1Y, but it should be corrected before the plan is used as
  an independent inventory or naming oracle.

### F4 — should-fix — older production output contains unlogged choices between unequal packets

- Where: main DU output produced before the collision check (`2cb091c`), and
  the closing recovery audit's sampled source paths in
  `~/codex_sandbox/du_closing_recovery/results.jsonl`.
- Status: verified.
- Scenario: among 1,516 distinct miniSEED source files encountered by the
  stratified closing audit, 42 contained an in-file overlap; 41 contained an
  unequal on-grid overlap, six contained an equal overlap, and none of this
  sample was off-grid. There were 105 overlapping packet pairs. Unequal cases
  occurred in sampled LILH 2023 (one file), MTON 2025 (one), PENW 2026 (12),
  PLYP 2025 (12) and PLYP 2026 (15). The old resolver silently retained the
  earlier-starting data. The closing witness still passed because the retained
  values exactly matched one source alternative; no samples were invented.
  This is a file sample drawn from audited windows, not a full archive census,
  so 41/1,516 must not be presented as the archive-wide prevalence.
- Smallest fix or check: record these five station-years as containing
  source-internal ambiguity and use F1's explicit logged rule going forward.
  Under the operator's greater-than-99% recovery target, do not rebuild these
  otherwise passing years solely to replace one source-valued deterministic
  choice with the same documented choice.

## Checked and found OK

- **No re-index is recommended for the excluded `.N.dmx` cohort under the
  stated greater-than-99% target.** I checked the sampling script and its raw
  results: 490 files, no unreadable files, and all sampled values already in
  staging except one CLV2 three-component file whose N and E traces (12,200
  samples total) were absent. One HKER file had about 6,000 values differing
  from the chosen copy but no time was absent. Every other sampled file was
  exact. Given the supplied census that all 181,740 files are at most 0.18% of
  DU station-minutes, this is a bounded accepted exclusion, not grounds for a
  broad rebuild. Document the CLV2 loss and HKER conflicting copy; retain the
  census and sample as evidence. The comparison is by orientation and time,
  so it proves payload coverage, not full SEED-identity equivalence.
- Release v2.4 ran from committed, clean pins. The six queue completion
  records show strict preflight with eqserver conversion code `3b687de`,
  `disk_to_sds` `5e41f17`, and `sudspy` `753feaf`; all conversion days are
  `ok` or genuine `no_files`, with no failed, blocked or QC-flagged day.
- The targeted header-name suite passes on the VM, including 100 Hz to `EH*`
  and 250 Hz to `DH*` for `sp_band_by_rate`.
- Independent spot checks across ADE2, GLN, MPTV, MYP and MVM1Y compared
  261,900 source samples with staging and found every sample exact. The MVM1Y
  mixed-rate day was checked separately on both sides of its rate change.
- The completed closing recovery audit processed all 1,578 planned minutes;
  1,514 were auditable, with 100% of authorised source samples recovered and
  a conservative 95% lower bound of 99.75%. No sampled station-year was below
  99%. That audit does not cover files excluded from the index, which is why
  the separate `.N.dmx` analysis above matters.

## Response


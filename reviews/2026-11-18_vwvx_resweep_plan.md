# Review: `docs/vwvx_resweep_plan.md`  (2026-11-18, reviewer: Codex)

## Verdict

FIX-THEN-PROCEED.

The basic approach is right: re-convert all VW and VX data into staging while
the EqServer source still exists, then make bounded decisions about overlaps
with LT. Do not delay that work for another large QA campaign. The fixes below
are gates on manifests, the handful of unproved input classes, naming, and
promotion of overlapping files—not a reason to repeat the DU study.

## Findings

### F1 — blocker — Sample counts cannot choose the better day-file

- Where: `docs/vwvx_resweep_plan.md:18-23,50-54`;
  `../sds_staging_ledger/apply.py:109-124,300-305`;
  `../sds_staging_ledger/lib/sds.py:91-114`
- Status: verified
- Scenario: `decide()` sums the MiniSEED record-header sample counts. If LT and
  staging contain the same number of samples at different times, or contain
  different values, LT is skipped without either difference being examined.
  Conversely, “staging has more samples” does not prove that staging contains
  everything in LT: the two files can cover different parts of a day. The
  proposed held/override set is therefore not a complete repair list, and the
  rule “staged supersedes where it has more data” can discard unique LT data.
  The later project evidence also shows that the approximately 320-unit
  “side-load” label is not itself proof of corruption: several supposedly
  affected Gecko/Minimus examples were byte-identical or faithfully reflected
  source packet gaps (`docs/agent_memory/project_class_a_vs_class_b_framing.md:20-31`).
- Smallest fix or check: keep sample count as the first pass. For every
  equal-count skip, cheaply compare the normalized timeline—stream id, exact
  rate, covered intervals and gap/overlap topology—from the MiniSEED headers.
  This catches the known timing/fragmentation failure class without decoding
  12 TB twice. Sample decoded values deterministically across each affected
  station-year and escalate any mismatch to a full canonical sample hash; no
  known v1 defect currently justifies hashing every equal-timeline day. For the
  much smaller held-override set, do the exact check: prove staging is a strict
  time-coverage superset and that overlapping decoded samples agree. Do not
  compare MiniSEED file bytes, because valid re-encoding can change them. This
  reads staging and LT only; it does not re-read EqServer or require another
  conversion campaign.

### F2 — blocker — Preserve live SeedLink days; still stage the EqServer copy

- Where: `docs/vwvx_resweep_plan.md:55-57`;
  `../sds_staging_ledger/apply.py:94-105,116-124`;
  `scan/run_production_promote.py:268-282`
- Status: verified
- Scenario: excluding everything after a station's live-feed start would leave
  EqServer-only gap fills uncaptured when the origin disappears. But replacing
  a 2025–26 LT day merely because EqServer staging has more samples can remove
  samples that exist only in the live SeedLink file; replacement is whole-file,
  not a union. The current promoter safely holds sample-count overrides for
  review, but the plan's stated resolution would then choose staging on count
  alone.
- Smallest fix or check: convert and retain all 2025–26 EqServer source now.
  Treat existing live SeedLink LT files as authoritative by default. Promote an
  overlapping EqServer day only when F1's comparison proves staging is a strict
  superset with identical overlapping samples. Otherwise keep LT and record the
  staged copy for a later explicit union; never use raw total count as the
  release decision.

### F3 — blocker — Target names need an explicit authority decision before conversion

- Where: `docs/vwvx_resweep_plan.md:25-36,43,58-60`; `CLAUDE.md:1804-1836`;
  `AGENTS.md` “Source of truth, in order”
- Status: verified
- Scenario: copying names directly from existing LT preserves any v1 naming
  mistake. Generating names solely from current rules can instead create a
  second day-channel beside the established LT file, because promotion sees a
  new path rather than a replacement. WLSH's CHO/CDO and HH*/HQ* differences
  are already concrete examples. A one-day check can also miss a channel or
  location epoch elsewhere in the year.
- Smallest fix or check: use the committed StationXML, registry and recorded
  operator decisions to choose the intended identity; use LT as compatibility
  evidence, not as the oracle. Before bulk conversion, compare the plan's full
  per-epoch `(location, channel, sample rate)` set with both source headers and
  LT. Resolve the two WLSH differences explicitly. If an intentional correction
  differs from LT, give it a held rename/replacement procedure; do not let it
  auto-write as an additional channel. There is no need to test 1–2 days in
  every unchanging year once this epoch inventory is complete.

### F4 — blocker — Build fresh manifests with the release parser

- Where: `docs/vwvx_resweep_plan.md:38-46`; `scan/level1.py:33-76,129-199`;
  `scan/coverage_planner.py:166-188`
- Status: suspected
- Scenario: the plan says plans are regenerated “from Level-1” but does not say
  that VW/VX Level-1 databases are rebuilt. Archived VW/VX databases predate
  several format-recognition changes. Reusing them can reproduce the DU relay
  failure mode: valid files never enter the candidate set, so conversion and
  its auditor can agree while both omit them.
- Smallest fix or check: make a fresh Stage-1 manifest under the exact pinned
  release a written prerequisite for every VW/VX station, then generate plans
  from those databases. A count-by-extension/exclusion comparison against the
  old databases is useful evidence, but is not a substitute for the fresh
  index. This is indexing work, not another waveform test campaign.

### F5 — blocker — `.wrno` needs a finite date census before the VW run

- Where: `docs/vwvx_resweep_plan.md:61-63`; `scan/level1.py:14-16,51,201-220`;
  `scan/test_parser.py:58-63`; `scan/coverage_planner.py:166-172`;
  `docs/scan2_planning_progress.md:65-69`
- Status: verified
- Scenario: Level 1 deliberately takes the date from the filename, the parser
  records a file named `1989-08-17 ... .wrno.dmx.gz` as 1989, and the planner
  fetches candidates by that filename-derived year. The committed discovery
  evidence contains about 970,000 `.wrno` files; most are correctly named
  2019–21 files, but about 10,000 carry 1989/1999 names. Those old-name files
  cannot enter a post-2012 decision even if their corrected header time is in
  scope. The parser test proves only that the flag is recognised, not that the
  waveform is assigned to the correct day.
- Smallest fix or check: header-scan all of the roughly 10,000 pre-2012-named
  `.wrno` files once. That is small enough for a complete census. Re-date those
  whose waveform headers are in scope, and explicitly exclude those whose
  headers are genuinely pre-2012 or duplicate a corrected copy. Then prove one
  ordinary 2019–21 `.wrno` day and one re-dated case, if any exists.

### F6 — should-fix — Four or five proof days cover the recorder gap; no new campaign is needed

- Where: `docs/vwvx_resweep_plan.md:61-63`;
  `docs/du_pre_sweep_notes.md:31-42`;
  `docs/workflow_v3_implementation_strategy.md:159-167`;
  `scan/level1.py:58-76`; `scan/phase3_driver.py:827-880,2011-2025`
- Status: verified
- Scenario: DU explicitly had no Minimus and almost certainly no Reftek RT130,
  so DU production does not prove those dispatch paths. The final release has
  also changed substantially since the old local performance corpus exercised
  MOE3, DDWB and SGWU. Mixed-separator input has three filename shapes, and no
  parser regression currently covers them. Launching all VW/VX without a
  current real day through these paths risks a class-wide omission.
- Smallest fix or check: at the final pinned release, run only this compact
  matrix through selection, conversion and the independent auditor:
  1. SGWU 2021-01-03 (RT130 plus mixed-separator/per-channel input); add one
     WILU `_001.mseed` day only if that SGWU day does not exercise the
     sequence-suffix form.
  2. DDWB 2021-06-15 (Minimus per-channel input).
  3. One WPSH 2019 `YYYY-MM-DD_HHMM-STA` day, because it is a distinct
     mixed-separator grammar; a real parser regression plus source/header
     inventory can replace this extra day if desired.
  4. MOE3 2012-06-24 (2012 Echo/VX).
  5. The `.wrno` case from F5.

  Require the expected identities, zero bad samples, and no unexplained source
  coverage loss. Existing WLSH/TYHS proving days remain useful for multi-copy
  and telemetry selection, but they do not replace these recorder-specific
  checks.

## Checked and found OK

- Converting all in-scope VW/VX station-years into shared staging is the right
  response to the origin-storage deadline. It avoids relying on the October
  sampled repair list and is reversible until promotion.
- VX first and VW worst-first is a sensible order: it obtains an early result
  from the smaller network and then prioritises known VW losses.
- `apply.py` remains dry-run by default, copies atomically, and never deletes LT.
- The production promoter currently holds any unit with a sample-count override
  rather than committing it automatically. That safety gate should remain; F1
  changes the evidence used to release a hold, not the basic hold mechanism.
- The plan correctly recognises that unexpected channel/location changes can
  create duplicate SDS identities and places naming before bulk conversion.
- No additional uniform multi-day study is justified. Fresh manifests, the
  finite `.wrno` census, and the targeted proof matrix above are sufficient
  launch gates.

## Response

